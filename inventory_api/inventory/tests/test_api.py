from decimal import Decimal
from django.core import mail
from django.urls import reverse
from rest_framework.test import APITestCase
from inventory.models import Company, Product, User, Warehouse, StockTransaction

class PasswordResetTests(APITestCase):
    def test_email_link_can_reset_the_password(self):
        user = User.objects.create_user(username="worker", email="worker@example.com", password="password123")
        response = self.client.post("/api/auth/password-reset/", {"email": user.email}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        reset_url = next(line for line in mail.outbox[0].body.splitlines() if line.startswith("http://"))

        response = self.client.get(reset_url, follow=True)
        self.assertContains(response, "new_password1")
        confirm_url = response.redirect_chain[-1][0]
        new_password = "new-password123"
        response = self.client.post(confirm_url, {"new_password1": new_password, "new_password2": new_password}, format="multipart", follow=True)
        self.assertContains(response, "Password reset complete")
        user.refresh_from_db()
        self.assertTrue(user.check_password(new_password))


class RegistrationTests(APITestCase):
    def test_registration_cannot_join_an_existing_company(self):
        Company.objects.create(name="Acme")
        data = {"username": "new-worker", "password": "password123", "company_name": "Acme"}

        response = self.client.post("/api/auth/register/", data, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("company_name", response.data)
        self.assertFalse(User.objects.filter(username="new-worker").exists())

        response = self.client.post("/api/auth/register/", {**data, "company_name": "New company"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.get(username="new-worker").company.name, "New company")


class InventoryApiTests(APITestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        self.user = User.objects.create_user(username="worker", password="password123", company=self.company)
        self.client.force_authenticate(self.user)
        self.warehouse = Warehouse.objects.create(company=self.company, name="Main", address="Kyiv")
        self.product = Product.objects.create(company=self.company, sku="SKU-1", name="Widget", base_price=Decimal("10.00"), min_stock_level=2)

    def test_in_and_out_update_dynamic_stock(self):
        self.client.post("/api/transactions/", {"product": self.product.id, "transaction_type": "IN", "quantity": 5, "destination_warehouse": self.warehouse.id, "occurred_at": "2026-01-01T10:00:00Z"}, format="json")
        response = self.client.get("/api/products/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["results"][0]["total_stock"], 5)
        out = self.client.post("/api/transactions/", {"product": self.product.id, "transaction_type": "OUT", "quantity": 2, "source_warehouse": self.warehouse.id, "occurred_at": "2026-01-01T11:00:00Z"}, format="json")
        self.assertEqual(out.status_code, 201)
        self.assertEqual(Product.objects.get(pk=self.product.pk).transactions.count(), 2)

    def test_out_cannot_exceed_stock(self):
        response = self.client.post("/api/transactions/", {"product": self.product.id, "transaction_type": "OUT", "quantity": 1, "source_warehouse": self.warehouse.id, "occurred_at": "2026-01-01T11:00:00Z"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("quantity", response.data)

    def test_product_delete_is_soft(self):
        response = self.client.delete(f"/api/products/{self.product.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertIsNotNone(Product.objects.get(pk=self.product.pk).deleted_at)

    def test_empty_company_cannot_access_another_companys_products(self):
        company = Company.objects.create(name="Empty")
        user = User.objects.create_user(username="other", company=company)
        self.client.force_authenticate(user)

        response = self.client.get("/api/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])

        product_url = f"/api/products/{self.product.pk}/"
        self.assertEqual(self.client.get(product_url).status_code, 404)
        self.assertEqual(self.client.patch(product_url, {"name": "Changed"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(product_url).status_code, 404)

    def test_stock_export_csv(self):
        response = self.client.get("/api/reports/stock-export/?format=csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])

    def test_warehouse_changes_refresh_the_cached_summary(self):
        summary_url = "/api/reports/summary/"
        self.assertEqual([row["warehouse"] for row in self.client.get(summary_url).data], ["Main"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/api/warehouses/", {"name": "West", "address": "Lviv"}, format="json")
        self.assertEqual(response.status_code, 201)
        warehouse_url = f'/api/warehouses/{response.data["id"]}/'
        self.assertCountEqual([row["warehouse"] for row in self.client.get(summary_url).data], ["Main", "West"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(warehouse_url, {"name": "Lviv"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual([row["warehouse"] for row in self.client.get(summary_url).data], ["Main", "Lviv"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(warehouse_url)
        self.assertEqual(response.status_code, 204)
        self.assertEqual([row["warehouse"] for row in self.client.get(summary_url).data], ["Main"])

    def test_movements_require_positive_quantity_and_active_product(self):
        payload = {
            "product": self.product.pk,
            "transaction_type": "IN",
            "quantity": 0,
            "destination_warehouse": self.warehouse.pk,
            "occurred_at": "2026-01-01T10:00:00Z",
        }
        self.assertEqual(self.client.post("/api/transactions/", payload).status_code, 400)
        self.client.delete(f"/api/products/{self.product.pk}/")
        payload["quantity"] = 1
        response = self.client.post("/api/transactions/", payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("product", response.data)
        self.assertFalse(StockTransaction.objects.exists())

    def test_filter_movements_by_either_warehouse(self):
        other = Warehouse.objects.create(company=self.company, name="Other", address="Lviv")
        for source, destination in [(self.warehouse, other), (other, self.warehouse), (None, other)]:
            StockTransaction.objects.create(
                company=self.company,
                product=self.product,
                transaction_type="TRANSFER" if source else "IN",
                quantity=1,
                source_warehouse=source,
                destination_warehouse=destination,
                occurred_at="2026-01-01T10:00:00Z",
                created_by=self.user,
            )
        response = self.client.get(f"/api/transactions/?warehouse={self.warehouse.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_out_below_minimum_emails_the_source_warehouse_admin(self):
        self.warehouse.admin_email = "warehouse@example.com"
        self.warehouse.save(update_fields=["admin_email"])
        occurred_at = "2026-01-01T10:00:00Z"
        StockTransaction.objects.create(
            company=self.company,
            product=self.product,
            transaction_type="IN",
            quantity=3,
            destination_warehouse=self.warehouse,
            occurred_at=occurred_at,
            created_by=self.user,
        )
        payload = {
            "product": self.product.pk,
            "transaction_type": "OUT",
            "quantity": 1,
            "source_warehouse": self.warehouse.pk,
            "occurred_at": occurred_at,
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/api/transactions/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 0)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/api/transactions/", payload, format="json")
            self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.warehouse.admin_email])
        self.assertIn(self.warehouse.name, mail.outbox[0].body)
        self.assertIn(self.product.sku, mail.outbox[0].body)
        self.assertIn("1 units left", mail.outbox[0].body)
