from rest_framework.routers import DefaultRouter
from django.urls import path, include
from .views import ProductViewSet, TransactionViewSet, WarehouseViewSet

router = DefaultRouter()
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("products", ProductViewSet, basename="product")
router.register("transactions", TransactionViewSet, basename="transaction")
urlpatterns = [path("", include(router.urls))]

