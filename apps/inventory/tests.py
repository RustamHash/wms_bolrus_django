from datetime import date
from decimal import Decimal

from django.test import Client, TestCase

from apps.accounts.models import User
from apps.catalog.models import Caliber, Client as Buyer, Grade, Product, Supplier
from apps.common.exceptions import WarehouseError
from apps.common.kg import as_kg
from apps.inventory.count import _lot_on_receipt
from apps.inventory.models import Balance, Movement, TransformDocument
from apps.inventory.services import has_downstream, post_in
from apps.inventory.transform import add_input, add_output, add_waste, create_document, post_document
from apps.planning.models import Task, TaskLine
from apps.receiving.models import Lot, Receipt
from apps.receiving.services import _lot
from apps.storage.models import Pallet


class TransformTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("transform-tester", "secret", name="Тест", role="admin")
        cls.supplier = Supplier.objects.create(name="Поставщик теста")
        cls.buyer = Buyer.objects.create(name="Клиент теста")
        cls.product = Product.objects.create(name="Картофель тест", code="TST-POT")
        cls.other = Product.objects.create(name="Очищенный тест", code="TST-PEE")
        cls.caliber = Caliber.objects.create(name="5+ тест")
        cls.grade = Grade.objects.create(name="1 сорт тест")
        cls.grade_b = Grade.objects.create(name="2 сорт тест")
        cls.receipt = Receipt.objects.create(
            supplier=cls.supplier, doc_date=date(2026, 10, 1), created_by=cls.user, status="closed"
        )
        cls.task = Task.objects.create(
            kind="receive", status="closed", supplier=cls.supplier, doc_date=date(2026, 10, 1), created_by=cls.user
        )
        cls.task_line = TaskLine.objects.create(
            task=cls.task, product=cls.product, caliber=cls.caliber, grade=cls.grade, qty_kg=Decimal("10.000")
        )

    def _pallet(self, qty="10.000", *, reserved=False):
        lot = Lot.objects.create(
            receipt=self.receipt,
            supplier=self.supplier,
            product=self.product,
            caliber=self.caliber,
            grade=self.grade,
            received_on=self.receipt.doc_date,
            source=Lot.Source.SUPPLY,
        )
        pallet = Pallet.objects.create(kind="inbound", status="closed", created_by=self.user, closed_by=self.user)
        if reserved:
            pallet.reserved_client = self.buyer
            pallet.save(update_fields=["reserved_client"])
        post_in(
            task=self.task,
            task_line=self.task_line,
            pallet=pallet,
            lot=lot,
            qty_kg=Decimal(qty),
            user=self.user,
            reserved=reserved,
        )
        return lot, pallet

    def _document(self, kind=TransformDocument.Kind.REWORK, tolerance="0"):
        return create_document(
            kind=kind,
            doc_date=date(2026, 10, 22),
            tolerance_kg=Decimal(tolerance),
            notes="",
            user=self.user,
        )

    def test_exact_mass_creates_child_and_writes_off_waste(self):
        lot, pallet = self._pallet()
        document = self._document()
        add_input(document, pallet.id)
        add_output(
            document,
            product_id=self.product.id,
            caliber_id=self.caliber.id,
            grade_id=self.grade_b.id,
            qty_kg=Decimal("8.000"),
            expires_on=date(2026, 12, 1),
        )
        add_waste(document, qty_kg=Decimal("2.000"), reason="гнил")
        before = Lot.objects.count()
        message = post_document(document, self.user)
        document.refresh_from_db()
        lot.refresh_from_db()
        self.assertEqual(document.status, TransformDocument.Status.POSTED)
        self.assertEqual(as_kg(document.ledger_waste_kg), Decimal("2.000"))
        self.assertIn("проведён", message.casefold())
        self.assertEqual(Lot.objects.count(), before + 1)
        child = lot.children.get()
        self.assertEqual(child.parent_id, lot.id)
        self.assertEqual(child.source, Lot.Source.REWORK)
        self.assertEqual(child.expires_on, date(2026, 12, 1))
        self.assertEqual(child.grade_id, self.grade_b.id)
        self.assertFalse(child.is_opening)
        self.assertTrue(child.number.startswith("ПТ-"))
        self.assertEqual(as_kg(Balance.objects.get(lot=lot).free_kg), Decimal("0.000"))
        self.assertEqual(as_kg(Balance.objects.get(lot=child).free_kg), Decimal("8.000"))
        waste = document.movements.get(kind=Movement.Kind.WASTE)
        self.assertEqual(waste.lot_id, lot.id)
        self.assertEqual(as_kg(waste.qty_kg), Decimal("2.000"))
        self.assertFalse(Lot.objects.filter(grade__name__icontains="отход").exists())
        inbound = Movement.objects.get(pallet=pallet, kind=Movement.Kind.IN)
        self.assertTrue(has_downstream(inbound))
        output = document.outputs.get()
        self.assertIsNotNone(output.pallet_id)
        self.assertFalse(has_downstream(Movement.objects.get(pallet=output.pallet, kind=Movement.Kind.IN)))
        self.assertEqual(_lot(self.receipt, self.product.id, self.caliber.id, self.grade.id).id, lot.id)

    def test_imbalance_does_not_post(self):
        lot, pallet = self._pallet()
        document = self._document()
        add_input(document, pallet.id)
        add_output(
            document,
            product_id=self.product.id,
            caliber_id=self.caliber.id,
            grade_id=self.grade.id,
            qty_kg=Decimal("7.000"),
            expires_on=None,
        )
        with self.assertRaises(WarehouseError):
            post_document(document, self.user)
        document.refresh_from_db()
        self.assertEqual(document.status, TransformDocument.Status.DRAFT)
        self.assertEqual(lot.children.count(), 0)
        self.assertEqual(as_kg(Balance.objects.get(lot=lot).free_kg), Decimal("10.000"))
        self.assertFalse(document.movements.exists())

    def test_tolerance_posts_book_waste(self):
        lot, pallet = self._pallet()
        document = self._document(tolerance="1")
        add_input(document, pallet.id)
        add_output(
            document,
            product_id=self.product.id,
            caliber_id=self.caliber.id,
            grade_id=self.grade.id,
            qty_kg=Decimal("8.000"),
            expires_on=None,
        )
        add_waste(document, qty_kg=Decimal("1.000"), reason="")
        post_document(document, self.user)
        document.refresh_from_db()
        self.assertEqual(as_kg(document.ledger_waste_kg), Decimal("2.000"))
        self.assertEqual(as_kg(document.wastes.get().qty_kg), Decimal("1.000"))
        self.assertEqual(as_kg(document.movements.get(kind=Movement.Kind.WASTE).qty_kg), Decimal("2.000"))
        self.assertEqual(as_kg(Balance.objects.get(lot=lot).free_kg), Decimal("0.000"))
        self.assertEqual(as_kg(Balance.objects.get(lot=lot.children.get()).free_kg), Decimal("8.000"))
        self.assertEqual(_lot(self.receipt, self.product.id, self.caliber.id, self.grade.id).id, lot.id)

    def test_reserved_pallet_returns_free_stock(self):
        lot, pallet = self._pallet(reserved=True)
        self.assertEqual(as_kg(Balance.objects.get(lot=lot).reserved_kg), Decimal("10.000"))
        document = self._document(kind=TransformDocument.Kind.WORKSHOP)
        add_input(document, pallet.id)
        add_output(
            document,
            product_id=self.other.id,
            caliber_id=self.caliber.id,
            grade_id=self.grade.id,
            qty_kg=Decimal("10.000"),
            expires_on=None,
        )
        post_document(document, self.user)
        lot.refresh_from_db()
        pallet.refresh_from_db()
        child = lot.children.get()
        self.assertEqual(child.source, Lot.Source.WORKSHOP)
        self.assertEqual(child.product_id, self.other.id)
        self.assertEqual(as_kg(Balance.objects.get(lot=lot).reserved_kg), Decimal("0.000"))
        self.assertEqual(as_kg(Balance.objects.get(lot=lot).free_kg), Decimal("0.000"))
        self.assertEqual(as_kg(Balance.objects.get(lot=child).free_kg), Decimal("10.000"))
        self.assertIsNone(pallet.reserved_client_id)

    def test_gain_within_tolerance_increases_stock(self):
        lot, pallet = self._pallet()
        document = self._document(tolerance="0.5")
        add_input(document, pallet.id)
        add_output(
            document,
            product_id=self.product.id,
            caliber_id=self.caliber.id,
            grade_id=self.grade.id,
            qty_kg=Decimal("10.500"),
            expires_on=None,
        )
        message = post_document(document, self.user)
        document.refresh_from_db()
        self.assertEqual(as_kg(document.ledger_waste_kg), Decimal("0.000"))
        self.assertFalse(document.movements.filter(kind=Movement.Kind.WASTE).exists())
        self.assertIn("допуске", message)
        self.assertEqual(as_kg(Balance.objects.get(lot=lot).free_kg), Decimal("0.000"))
        self.assertEqual(as_kg(Balance.objects.get(lot=lot.children.get()).free_kg), Decimal("10.500"))

    def test_full_waste_does_not_create_lot(self):
        lot, pallet = self._pallet()
        document = self._document()
        add_input(document, pallet.id)
        add_waste(document, qty_kg=Decimal("10.000"), reason="всё в отход")
        before = Lot.objects.count()
        post_document(document, self.user)
        self.assertEqual(Lot.objects.count(), before)
        self.assertEqual(lot.children.count(), 0)
        self.assertFalse(document.movements.filter(kind=Movement.Kind.TRANSFORM_OUT).exists())
        waste = document.movements.get(kind=Movement.Kind.WASTE)
        self.assertEqual(waste.lot_id, lot.id)
        self.assertEqual(as_kg(Balance.objects.get(lot=lot).free_kg), Decimal("0.000"))

    def test_supply_lot_still_comes_from_receipt(self):
        receipt = Receipt.objects.create(
            supplier=self.supplier, doc_date=date(2026, 10, 3), created_by=self.user, status="open"
        )
        made = _lot(receipt, self.product.id, self.caliber.id, self.grade.id)
        self.assertEqual(made.source, Lot.Source.SUPPLY)
        self.assertIsNone(made.parent_id)
        self.assertFalse(made.is_opening)
        self.assertEqual(made.number, f"ПТ-{made.id:06d}")
        self.assertEqual(_lot(receipt, self.product.id, self.caliber.id, self.grade.id).id, made.id)

    def test_opening_lot_is_marked(self):
        receipt = Receipt.objects.create(
            supplier=self.supplier, doc_date=date(2026, 10, 2), created_by=self.user, status="closed"
        )
        lot = _lot_on_receipt(receipt, self.product.id, self.caliber.id, self.grade.id)
        self.assertEqual(lot.source, Lot.Source.INVENTORY)
        self.assertTrue(lot.is_opening)
        self.assertEqual(lot.number, f"ПТ-{lot.id:06d}")
        self.assertEqual(_lot_on_receipt(receipt, self.product.id, self.caliber.id, self.grade.id).id, lot.id)

    def test_passport_save_keeps_accounting_grade(self):
        lot, _pallet = self._pallet()
        grade_id = lot.grade_id
        client = Client()
        client.force_login(self.user)
        response = client.post(
            f"/lots/{lot.id}",
            {"expires_on": "2026-12-31", "passport_grade": "экстра с бумаги", "passport_supplier": "Поле"},
        )
        self.assertEqual(response.status_code, 302)
        lot.refresh_from_db()
        self.assertEqual(lot.expires_on, date(2026, 12, 31))
        self.assertEqual(lot.passport_grade, "экстра с бумаги")
        self.assertEqual(lot.passport_supplier, "Поле")
        self.assertEqual(lot.grade_id, grade_id)
        self.assertEqual(lot.supplier_id, self.supplier.id)
        page = client.get(f"/lots/{lot.id}")
        self.assertContains(page, lot.number)
        self.assertContains(page, "экстра с бумаги")
        created = client.post(
            "/transform",
            {"kind": "rework", "doc_date": "2026-10-22", "tolerance_kg": "0", "notes": ""},
        )
        self.assertEqual(created.status_code, 302)
        self.assertIn("/transform/", created["Location"])
