from django.contrib.admin import ModelAdmin

from apps.accounts.access import ACTIONS

from apps.accounts.models import AdminRoleGrant, AdminUserGrant, Position, User
from apps.catalog.models import Caliber, Grade, Packaging, Product, ProductGroup, Seller, Supplier
from apps.inventory.models import Balance, Movement, TransformDocument
from apps.planning.models import Order, OrderLine, PurchaseOrder, PurchaseOrderLine, Task, TaskLine
from apps.printing.models import Printer
from apps.receiving.models import Lot, Receipt
from apps.logistics.models import Driver, Route, RouteOrder, Vehicle
from apps.shipping.models import Shipment
from apps.storage.models import HandlingUnit, Pallet, WarehouseCell, WarehouseZone
from config.admin_site import wms_admin


class OpenAdmin(ModelAdmin):
    def _permit(self, request, action):
        user = request.user
        if not user.is_authenticated or not user.is_active:
            return False
        if user.is_superuser or getattr(user, "role", "") == "admin":
            return True
        if not getattr(user, "access_managed", False):
            return True
        opts = self.opts
        return user.has_perm(f"{opts.app_label}.{action}_{opts.model_name}")

    def has_module_permission(self, request):
        return any(self._permit(request, action) for action, _label in ACTIONS)

    def has_view_permission(self, request, obj=None):
        return self._permit(request, "view") or self._permit(request, "change")

    def has_add_permission(self, request):
        return self._permit(request, "add")

    def has_change_permission(self, request, obj=None):
        return self._permit(request, "change")

    def has_delete_permission(self, request, obj=None):
        return self._permit(request, "delete")


class ReadAdmin(OpenAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class UserAdmin(OpenAdmin):
    list_display = ("name", "login", "role", "position", "is_active")
    fields = ("name", "last_name", "first_name", "middle_name", "login", "role", "position", "tsd_code", "access_receive", "access_pick", "access_ship", "access_move", "is_active")


def _show(model, fields):
    admin = type(f"{model.__name__}Admin", (OpenAdmin,), {"list_display": fields})
    wms_admin.register(model, admin)


wms_admin.register(User, UserAdmin)
wms_admin.register(
    Position,
    type(
        "PositionAdmin",
        (OpenAdmin,),
        {"list_display": ("name", "access_receive", "access_pick", "access_ship", "access_move", "access_shift", "is_active")},
    ),
)
wms_admin.register(AdminRoleGrant, type("RoleGrantAdmin", (OpenAdmin,), {"list_display": ("role",)}))
wms_admin.register(AdminUserGrant, type("UserGrantAdmin", (OpenAdmin,), {"list_display": ("user",)}))

for model, fields in (
    (ProductGroup, ("name",)),
    (Product, ("code", "name", "group")),
    (Caliber, ("name", "sort_order")),
    (Grade, ("name", "sort_order")),
    (Packaging, ("name", "tare_kg")),
    (Supplier, ("name",)),
    (Seller, ("code", "name")),
    (Printer, ("name", "printer_type", "host", "is_default")),
    (Order, ("number", "consignee", "seller", "ship_date", "status")),
    (OrderLine, ("order", "product", "qty_kg")),
    (PurchaseOrder, ("number", "supplier", "doc_date", "status")),
    (PurchaseOrderLine, ("order", "product", "qty_kg")),
    (Task, ("number", "kind", "status")),
    (TaskLine, ("task", "product", "qty_kg")),
    (Receipt, ("number", "supplier", "doc_date", "status")),
    (Lot, ("number", "source", "product", "supplier", "received_on", "expires_on", "parent", "is_opening")),
    (Shipment, ("number", "client_name", "order")),
    (WarehouseZone, ("code", "name", "barcode")),
    (WarehouseCell, ("zone", "code", "name", "barcode")),
    (Pallet, ("number", "kind", "status")),
    (HandlingUnit, ("barcode", "status", "net_kg")),
    (Vehicle, ("plate_number", "brand_model", "load_capacity_kg", "pallet_capacity", "is_active")),
    (Driver, ("full_name", "phone", "vehicle", "is_active")),
    (Route, ("route_date", "name", "driver", "status", "planned_load_at", "docked_at", "actual_load_at")),
    (RouteOrder, ("route", "order", "position")),
):
    _show(model, fields)

wms_admin.register(Movement, type("MovementAdmin", (ReadAdmin,), {"list_display": ("pallet", "kind", "lot", "qty_kg")}))
wms_admin.register(
    TransformDocument,
    type("TransformDocumentAdmin", (ReadAdmin,), {"list_display": ("number", "kind", "status", "doc_date")}),
)
wms_admin.register(Balance, type("BalanceAdmin", (ReadAdmin,), {"list_display": ("lot", "free_kg", "reserved_kg", "picked_kg")}))
