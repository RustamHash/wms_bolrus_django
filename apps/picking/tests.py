from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.catalog.models import Caliber, Consignee, Grade, Packaging, Product, Seller, Supplier
from apps.common.kg import as_kg
from apps.inventory.models import Balance, Movement
from apps.inventory.services import has_downstream, pallet_room, physical_left, post_in
from apps.picking.models import PickAllocation
from apps.picking.services import pick_allocation, pick_ready_task, reserve_for_task
from apps.planning.models import Order, OrderLine, Task, TaskLine
from apps.planning.services import close_task
from apps.receiving.models import Lot, Receipt
from apps.shipping.services import ship_pallet
from apps.storage.models import HandlingUnit, Pallet


class PartialPickTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("pick-tester", "secret", name="Тест", role="admin")
        cls.supplier = Supplier.objects.create(name="Поставщик отбора")
        cls.consignee = Consignee.objects.create(name="Грузополучатель отбора")
        cls.seller = Seller.objects.create(code="SEL-PICK", name="Продавец отбора")
        cls.product = Product.objects.create(name="Картофель отбор", code="PICK-POT")
        cls.caliber = Caliber.objects.create(name="5+ отбор")
        cls.grade = Grade.objects.create(name="1 сорт отбор")
        cls.packaging = Packaging.objects.create(name="Сетка отбор", tare_kg=Decimal("0.200"))
        cls.receipt = Receipt.objects.create(
            supplier=cls.supplier, doc_date=date(2026, 10, 1), created_by=cls.user, status="closed"
        )
        cls.receive = Task.objects.create(
            kind="receive", status="closed", supplier=cls.supplier, doc_date=date(2026, 10, 1), created_by=cls.user
        )
        cls.receive_line = TaskLine.objects.create(
            task=cls.receive, product=cls.product, caliber=cls.caliber, grade=cls.grade, qty_kg=Decimal("800.000")
        )

    def _order(self, qty, number):
        order = Order.objects.create(
            number=number,
            consignee=self.consignee,
            seller=self.seller,
            ship_date=date(2026, 10, 22),
            created_by=self.user,
        )
        line = OrderLine.objects.create(order=order, product=self.product, qty_kg=Decimal(qty))
        task = Task.objects.create(order=order, kind="pick", status="created", created_by=self.user)
        task_line = TaskLine.objects.create(
            task=task,
            order_line=line,
            product=self.product,
            caliber=self.caliber,
            grade=self.grade,
            qty_kg=Decimal(qty),
        )
        return task, task_line

    def _pallet(self, qty):
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
        post_in(
            task=self.receive,
            task_line=self.receive_line,
            pallet=pallet,
            lot=lot,
            qty_kg=Decimal(qty),
            user=self.user,
        )
        unit = HandlingUnit.objects.create(
            receipt=self.receipt,
            lot=lot,
            packaging=self.packaging,
            gross_kg=Decimal(qty),
            net_kg=Decimal(qty),
            grade=self.grade,
            pallet=pallet,
            status="received",
        )
        return pallet, unit

    def test_plan_takes_part_of_pallet_and_pick_keeps_the_rest(self):
        pallet, unit = self._pallet("800.000")
        task, line = self._order("100.000", "ЗК-PICK-1")
        text = reserve_for_task(task)
        task.refresh_from_db()
        alloc = PickAllocation.objects.get(task_line=line, pallet=pallet)
        self.assertEqual(as_kg(alloc.qty_kg), Decimal("100.000"))
        self.assertIn("100", text)
        self.assertEqual(task.status, "planned")
        self.assertIsNone(pallet.reserved_line_id)
        self.assertEqual(pallet_room(pallet.id), Decimal("700.000"))

        movement = pick_allocation(allocation_id=alloc.id, user=self.user, qty_kg=Decimal("30.000"))
        self.assertEqual(as_kg(movement.qty_kg), Decimal("30.000"))
        self.assertEqual(as_kg(Balance.objects.get(lot=movement.lot).free_kg), Decimal("770.000"))
        self.assertEqual(as_kg(Balance.objects.get(lot=movement.lot).picked_kg), Decimal("0.000"))
        self.assertEqual(as_kg(Balance.objects.get(lot=movement.lot).reserved_kg), Decimal("0.000"))
        unit.refresh_from_db()
        self.assertEqual(unit.status, "received")
        self.assertFalse(has_downstream(Movement.objects.get(pallet=pallet, kind="in")))
        task.refresh_from_db()
        self.assertEqual(task.status, "in_work")

        pick_allocation(allocation_id=alloc.id, user=self.user)
        task.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual(task.status, "picked")
        task.order.refresh_from_db()
        self.assertEqual(task.order.status, "closed")
        self.assertEqual(unit.status, "received")
        self.assertFalse(has_downstream(Movement.objects.get(pallet=pallet, kind="in")))
        self.assertEqual(pallet_room(pallet.id), Decimal("700.000"))
        self.assertEqual(
            Movement.objects.filter(pallet=pallet, kind="pick").count(),
            2,
        )

        other, _other_line = self._order("50.000", "ЗК-PICK-2")
        reserve_for_task(other)
        second = PickAllocation.objects.get(task_line__task=other, pallet=pallet)
        self.assertEqual(as_kg(second.qty_kg), Decimal("50.000"))

    def test_force_close_keeps_unpicked_in_plan(self):
        pallet, _unit = self._pallet("10.000")
        task, _line = self._order("40.000", "ЗК-PICK-3")
        reserve_for_task(task)
        task.refresh_from_db()
        self.assertEqual(task.status, "partial")
        close_task(task)
        task.refresh_from_db()
        task.order.refresh_from_db()
        self.assertEqual(task.status, "closed")
        self.assertEqual(task.order.status, "partial")
        self.assertEqual(Movement.objects.filter(task=task, kind="pick").count(), 0)
        self.assertEqual(PickAllocation.objects.filter(task_line__task=task).count(), 0)
        self.assertEqual(pallet_room(pallet.id), Decimal("10.000"))

    def test_ship_partial_pick_leaves_units(self):
        pallet, unit = self._pallet("80.000")
        task, line = self._order("20.000", "ЗК-PICK-4")
        reserve_for_task(task)
        alloc = PickAllocation.objects.get(task_line=line)
        pick_ready_task(task, self.user)
        pick = Movement.objects.get(task_line=line, kind="pick")
        ship = Task.objects.create(order=task.order, kind="ship", status="to_ship", created_by=self.user)
        ship_line = TaskLine.objects.create(
            task=ship, order_line=line.order_line, product=self.product, qty_kg=Decimal("20.000")
        )
        ship_pallet(
            task_line_id=ship_line.id,
            pallet_id=pallet.id,
            user=self.user,
            confirmed=True,
            movement_id=pick.id,
        )
        unit.refresh_from_db()
        self.assertEqual(unit.status, "received")
        self.assertEqual(physical_left(pallet.id), Decimal("60.000"))
        self.assertEqual(as_kg(alloc.qty_kg), Decimal("20.000"))
        self.assertEqual(Movement.objects.filter(source=pick, kind="out").count(), 1)
