from rest_framework.routers import DefaultRouter
from django.urls import path, include
from .views import ProductViewSet, TransactionViewSet, WarehouseViewSet, export_view, report_view

router = DefaultRouter()
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("products", ProductViewSet, basename="product")
router.register("transactions", TransactionViewSet, basename="transaction")
urlpatterns = [path("reports/summary/", report_view), path("reports/stock-export/", export_view), path("", include(router.urls))]

