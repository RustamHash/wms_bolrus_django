"""
Очистка движений, заданий, остатков, паллет и партий.
Справочники (товары, калибры, сорта, поставщики, упаковки, ячейки, водители, машины) НЕ трогает.

Запуск:
    python scripts/reset_movements.py              # сухой прогон
    python scripts/reset_movements.py --apply      # реальное удаление
"""
import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.db.models.deletion import ProtectedError

from apps.inventory.models import (
    Balance,
    InventoryDocument,
    InventoryLine,
    Movement,
    TransformDocument,
)
from apps.logistics.models import Route, RouteOrder
from apps.planning.models import (
    Order,
    OrderLine,
    OrderRevision,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderRevision,
    Task,
    TaskLine,
)
from apps.picking.models import PickAllocation
from apps.receiving.models import Lot, Receipt
from apps.shipping.models import Allocation
from apps.storage.models import HandlingUnit, Pallet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="реально удалить")
    args = parser.parse_args()
    dry_run = not args.apply

    print("РЕЖИМ:", "СУХОЙ ПРОГОН (ничего не удаляется)" if dry_run else "УДАЛЕНИЕ")
    print()

    with transaction.atomic():
        # ---------- 1. Movement: сначала обнулить source (самоссылка PROTECT) ----------
        movement_count = Movement.objects.count()
        print(f"  {'Movement (всего)':24s} {movement_count:>8d}")
        if movement_count:
            with_source = Movement.objects.filter(source__isnull=False).count()
            print(f"  {'  из них со source':24s} {with_source:>8d}")
            if not dry_run:
                Movement.objects.filter(source__isnull=False).update(source=None)
                Movement.objects.all().delete()

        # ---------- 2. Lot: обнулить parent (самоссылка PROTECT) ----------
        lot_count = Lot.objects.count()
        if lot_count:
            with_parent = Lot.objects.filter(parent__isnull=False).count()
            print(f"  {'Lot (всего)':24s} {lot_count:>8d}")
            if with_parent:
                print(f"  {'  из них с parent':24s} {with_parent:>8d}")
            if not dry_run and with_parent:
                Lot.objects.filter(parent__isnull=False).update(parent=None)

        # ---------- 3. Остальное — «дети → родители» ----------
        steps = [
            ("Allocation", Allocation),
            ("PickAllocation", PickAllocation),
            ("InventoryLine", InventoryLine),
            ("InventoryDocument", InventoryDocument),
            ("TransformDocument", TransformDocument),
            ("RouteOrder", RouteOrder),
            ("Route", Route),
            ("HandlingUnit", HandlingUnit),
            ("Pallet", Pallet),
            ("TaskLine", TaskLine),
            ("Task", Task),
            ("OrderLine", OrderLine),
            ("OrderRevision", OrderRevision),
            ("Order", Order),
            ("PurchaseOrderLine", PurchaseOrderLine),
            ("PurchaseOrderRevision", PurchaseOrderRevision),
            ("PurchaseOrder", PurchaseOrder),
            ("Balance", Balance),      # ← ДО Lot
            ("Lot", Lot),              # ← после Balance
            ("Receipt", Receipt),
        ]
        for name, model in steps:
            count = model.objects.count()
            print(f"  {name:24s} {count:>8d}")
            if count and not dry_run:
                try:
                    model.objects.all().delete()
                except ProtectedError as exc:
                    print(f"    ! ProtectedError при удалении {name}:")
                    print(f"      {exc}")
                    raise
        if dry_run:
            transaction.set_rollback(True)

    print()
    if dry_run:
        print("Это был сухой прогон. Для удаления запустите:")
        print("  python scripts/reset_movements.py --apply")
    else:
        print("Готово. Движения, задания, остатки, паллеты и партии удалены.")
        print("Справочники сохранены.")


if __name__ == "__main__":
    main()