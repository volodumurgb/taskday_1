import time

import pandas as pd
from django.contrib.auth import login, logout
from django.contrib.auth.forms import PasswordResetForm
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.utils import timezone
from django_filters import rest_framework as filters
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .models import Product, StockTransaction, User, Warehouse
from .serializers import LoginSerializer, ProductSerializer, RegisterSerializer, StockTransactionSerializer, WarehouseSerializer
from .services import REPORT_CACHE_KEY, invalidate_report, products_with_stock, stock_expression

def company_queryset(user, model):
    return model.objects.filter(company=user.company)

@api_view(["POST"])
@permission_classes([AllowAny])
def register_view(request):
    serializer = RegisterSerializer(data=request.data); serializer.is_valid(raise_exception=True); user = serializer.save(); login(request, user)
    return Response({"id": user.id, "username": user.username}, status=status.HTTP_201_CREATED)

@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    serializer = LoginSerializer(data=request.data); serializer.is_valid(raise_exception=True); login(request, serializer.validated_data["user"])
    return Response({"detail": "Logged in."})

@api_view(["POST"])
def logout_view(request):
    logout(request); return Response(status=status.HTTP_204_NO_CONTENT)

@api_view(["POST"])
@permission_classes([AllowAny])
def password_reset_view(request):
    form = PasswordResetForm(request.data)
    if form.is_valid():
        form.save(use_https=request.is_secure(), email_template_name="registration/password_reset_email.html", request=request)
    return Response({"detail": "If the address exists, reset instructions were sent."})

class WarehouseViewSet(viewsets.ModelViewSet):
    serializer_class = WarehouseSerializer
    def get_queryset(self): return company_queryset(self.request.user, Warehouse)

    def perform_create(self, serializer):
        warehouse = serializer.save(company=self.request.user.company)
        transaction.on_commit(lambda: invalidate_report(warehouse.company_id))

    def perform_update(self, serializer):
        warehouse = serializer.save()
        transaction.on_commit(lambda: invalidate_report(warehouse.company_id))

    def perform_destroy(self, instance):
        company_id = instance.company_id
        try:
            instance.delete()
        except ProtectedError as exc:
            raise serializers.ValidationError("This warehouse has stock movements.") from exc
        transaction.on_commit(lambda: invalidate_report(company_id))

class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    search_fields = ("name", "sku")
    ordering_fields = ("base_price", "total_stock", "name")
    ordering = ("id",)
    def get_queryset(self): return products_with_stock(company_queryset(self.request.user, Product).filter(deleted_at__isnull=True))
    def perform_create(self, serializer): serializer.save(company=self.request.user.company)
    def perform_update(self, serializer):
        product = serializer.save()
        transaction.on_commit(lambda: invalidate_report(product.company_id))

    def perform_destroy(self, instance):
        instance.deleted_at = timezone.now()
        instance.save(update_fields=["deleted_at"])


class TransactionFilter(filters.FilterSet):
    warehouse = filters.NumberFilter(method="filter_warehouse")

    class Meta:
        model = StockTransaction
        fields = {"transaction_type": ["exact"], "source_warehouse": ["exact"], "destination_warehouse": ["exact"], "occurred_at": ["gte", "lte"]}

    def filter_warehouse(self, queryset, name, value):
        return queryset.filter(Q(source_warehouse_id=value) | Q(destination_warehouse_id=value))

class TransactionViewSet(viewsets.ModelViewSet):
    serializer_class = StockTransactionSerializer
    http_method_names = ["get", "post", "head", "options"]
    filterset_class = TransactionFilter
    ordering_fields = ("occurred_at", "created_at")
    ordering = ("-occurred_at", "-id")
    def get_queryset(self): return company_queryset(self.request.user, StockTransaction).select_related("product", "source_warehouse", "destination_warehouse")
    def perform_create(self, serializer):
        rate_key = f"inventory:rate:{self.request.user.pk}:{int(time.time() // 60)}"
        cache.add(rate_key, 0, timeout=65)
        if cache.incr(rate_key) > 50:
            raise serializers.ValidationError("Transaction rate limit exceeded: maximum 50 per minute.")
        data = serializer.validated_data; product = data["product"]; kind = data["transaction_type"]; qty = data["quantity"]; source = data.get("source_warehouse"); destination = data.get("destination_warehouse")
        if product.company_id != self.request.user.company_id or (source and source.company_id != self.request.user.company_id) or (destination and destination.company_id != self.request.user.company_id):
            raise serializers.ValidationError("All resources must belong to your company.")
        if kind == "IN" and not destination: raise serializers.ValidationError({"destination_warehouse": "Required for IN."})
        if kind in ("OUT", "TRANSFER") and not source: raise serializers.ValidationError({"source_warehouse": "Required for OUT and TRANSFER."})
        if kind == "OUT" and destination: raise serializers.ValidationError({"destination_warehouse": "Not used for OUT."})
        if kind == "IN" and source: raise serializers.ValidationError({"source_warehouse": "Not used for IN."})
        if kind == "TRANSFER" and (not destination or destination == source): raise serializers.ValidationError({"destination_warehouse": "A different destination is required."})
        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=product.pk)
            if product.deleted_at is not None:
                raise serializers.ValidationError({"product": "This product has been deleted."})
            if source:
                current = Product.objects.filter(pk=product.pk).annotate(stock=stock_expression(source.pk)).values_list("stock", flat=True).get()
                if current < qty: raise serializers.ValidationError({"quantity": f"Insufficient stock ({current} available)."})
            saved = serializer.save(company=self.request.user.company, created_by=self.request.user)
            transaction.on_commit(lambda: invalidate_report(saved.company_id))
            if kind == "OUT" and source.admin_email:
                remaining = current - qty
                if remaining < product.min_stock_level:
                    message = (
                        f"{source.name}: {product.name} ({product.sku}) has {remaining} units left. "
                        f"Minimum stock level: {product.min_stock_level}."
                    )
                    transaction.on_commit(lambda: send_mail(
                        "Low stock alert",
                        message,
                        settings.DEFAULT_FROM_EMAIL,
                        [source.admin_email],
                        fail_silently=True,
                    ))

@api_view(["GET"])
def report_view(request):
    key = REPORT_CACHE_KEY.format(company_id=request.user.company_id); cached = cache.get(key)
    if cached is not None: return Response(cached)
    rows = []
    for warehouse in company_queryset(request.user, Warehouse):
        products = products_with_stock(company_queryset(request.user, Product)).annotate(warehouse_stock=stock_expression(warehouse.pk))
        total = sum((p.warehouse_stock * p.base_price for p in products), 0)
        rows.append({"warehouse_id": warehouse.id, "warehouse": warehouse.name, "total_value": str(total)})
    cache.set(key, rows, timeout=300); return Response(rows)

@api_view(["GET"])
def export_view(request):
    products = products_with_stock(company_queryset(request.user, Product)); data = [{"sku": p.sku, "name": p.name, "total_stock": p.total_stock, "total_value": str(p.total_value)} for p in products]
    if request.query_params.get("format") == "csv":
        response = HttpResponse(content_type="text/csv"); response["Content-Disposition"] = 'attachment; filename="stock-report.csv"'; pd.DataFrame(data, columns=["sku", "name", "total_stock", "total_value"]).to_csv(response, index=False); return response
    return Response(data)

