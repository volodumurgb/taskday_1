from django.core.cache import cache
from django.db.models import Case, F, IntegerField, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from .models import Product

REPORT_CACHE_KEY = "inventory:report:{company_id}"

def stock_expression(warehouse_id=None):
    if warehouse_id:
        incoming = Q(transactions__destination_warehouse_id=warehouse_id, transactions__transaction_type__in=["IN", "TRANSFER"])
        outgoing = Q(transactions__source_warehouse_id=warehouse_id)
        return Coalesce(Sum(Case(When(incoming, then=F("transactions__quantity")), When(outgoing, then=-F("transactions__quantity")), default=Value(0), output_field=IntegerField())), Value(0))
    incoming = Sum(Case(When(transactions__transaction_type="IN", then=F("transactions__quantity")), default=Value(0), output_field=IntegerField()))
    outgoing = Sum(Case(When(transactions__transaction_type="OUT", then=F("transactions__quantity")), default=Value(0), output_field=IntegerField()))
    return Coalesce(incoming - outgoing, Value(0))

def products_with_stock(queryset=None):
    if queryset is None:
        queryset = Product.objects.filter(deleted_at__isnull=True)
    return queryset.annotate(total_stock=stock_expression()).annotate(total_value=F("total_stock") * F("base_price"))

def invalidate_report(company_id):
    cache.delete(REPORT_CACHE_KEY.format(company_id=company_id))

