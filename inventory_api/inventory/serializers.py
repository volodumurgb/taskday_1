from django.contrib.auth import authenticate
from django.db import transaction
from rest_framework import serializers
from .models import Company, Product, StockTransaction, User, Warehouse
from .services import stock_expression

class RegisterSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(write_only=True, max_length=200)
    password = serializers.CharField(write_only=True, min_length=8)
    class Meta:
        model = User
        fields = ("username", "email", "password", "company_name")
    @transaction.atomic
    def create(self, data):
        company, created = Company.objects.get_or_create(name=data.pop("company_name"))
        if not created:
            raise serializers.ValidationError({"company_name": "A company with this name already exists."})
        return User.objects.create_user(company=company, **data)

class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)
    def validate(self, attrs):
        user = authenticate(username=attrs["username"], password=attrs["password"])
        if not user:
            raise serializers.ValidationError("Invalid credentials.")
        attrs["user"] = user
        return attrs

class WarehouseSerializer(serializers.ModelSerializer):
    stock = serializers.SerializerMethodField()
    class Meta:
        model = Warehouse
        fields = ("id", "name", "address", "stock")
    def get_stock(self, warehouse):
        rows = Product.objects.filter(company=warehouse.company, deleted_at__isnull=True).annotate(stock=stock_expression(warehouse.id))
        return [{"product_id": row.id, "sku": row.sku, "name": row.name, "stock": row.stock, "value": str(row.stock * row.base_price)} for row in rows]

class ProductSerializer(serializers.ModelSerializer):
    total_stock = serializers.IntegerField(read_only=True)
    total_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    class Meta:
        model = Product
        fields = ("id", "sku", "name", "description", "base_price", "min_stock_level", "total_stock", "total_value")

class StockTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockTransaction
        fields = ("id", "product", "transaction_type", "quantity", "source_warehouse", "destination_warehouse", "occurred_at")

