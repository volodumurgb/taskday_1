from django.contrib.auth.models import AbstractUser
from django.db import models

class Company(models.Model):
    name = models.CharField(max_length=200, unique=True)

class User(AbstractUser):
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE, related_name="employees")

class Warehouse(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="warehouses")
    name = models.CharField(max_length=200)
    address = models.TextField()
    class Meta:
        unique_together = ("company", "name")

class Product(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="products")
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    base_price = models.DecimalField(max_digits=12, decimal_places=2)
    min_stock_level = models.PositiveIntegerField(default=0)
    deleted_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("company", "sku"), name="unique_company_sku")]

class StockTransaction(models.Model):
    class Type(models.TextChoices):
        IN = "IN", "Incoming"
        OUT = "OUT", "Outgoing"
        TRANSFER = "TRANSFER", "Transfer"
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="transactions")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="transactions")
    transaction_type = models.CharField(max_length=8, choices=Type.choices)
    quantity = models.PositiveIntegerField()
    source_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="outgoing_transactions")
    destination_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="incoming_transactions")
    occurred_at = models.DateTimeField()
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

