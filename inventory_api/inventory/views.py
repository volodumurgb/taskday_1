import time
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import PasswordResetForm
from django.core.cache import cache
from django.core.mail import mail_admins
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .models import Product, StockTransaction, User, Warehouse
from .serializers import LoginSerializer, ProductSerializer, RegisterSerializer, StockTransactionSerializer, WarehouseSerializer
from .services import invalidate_report, products_with_stock, stock_expression

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
        instance.delete()
        transaction.on_commit(lambda: invalidate_report(company_id))

class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    search_fields = ("name", "sku")
    ordering_fields = ("base_price", "total_stock", "name")
    ordering = ("id",)
    def get_queryset(self): return products_with_stock(company_queryset(self.request.user, Product).filter(deleted_at__isnull=True))
    def perform_create(self, serializer): serializer.save(company=self.request.user.company)
    def perform_destroy(self, instance): instance.deleted_at = timezone.now(); instance.save(update_fields=["deleted_at"])

class TransactionViewSet(viewsets.ModelViewSet):
    serializer_class = StockTransactionSerializer
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = {"transaction_type": ["exact"], "source_warehouse": ["exact"], "destination_warehouse": ["exact"], "occurred_at": ["gte", "lte"]}
    ordering_fields = ("occurred_at", "created_at")
    ordering = ("-occurred_at", "-id")
    def get_queryset(self): return company_queryset(self.request.user, StockTransaction).select_related("product", "source_warehouse", "destination_warehouse")
    def perform_create(self, serializer):
        rate_key = f"inventory:rate:{self.request.user.pk}:{int(time.time() // 60)}"
        attempts = cache.get(rate_key, 0)
        if attempts >= 50:
            raise serializers.ValidationError("Transaction rate limit exceeded: maximum 50 per minute.")
        cache.set(rate_key, attempts + 1, timeout=65)
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
            invalidate_report(saved.company_id)
            if kind == "OUT":
                remaining = Product.objects.filter(pk=product.pk).annotate(stock=stock_expression(source.pk)).values_list("stock", flat=True).get()
                if remaining < product.min_stock_level: mail_admins("Low stock alert", f"{product.sku} has {remaining} left at {source.name}.", fail_silently=True)
