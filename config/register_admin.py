from django.contrib.admin import ModelAdmin

from apps.accounts.models import AdminRoleGrant, AdminUserGrant, User
from apps.catalog.models import Caliber, Client, Grade, Packaging, Product, ProductGroup, Seller, Supplier
from apps.inventory.models import Balance, Movement
from apps.planning.models import Order, OrderLine, PurchaseOrder, PurchaseOrderLine, Task, TaskLine
from apps.printing.models import Printer
from apps.receiving.models import Receipt
from apps.shipping.models import Shipment
from apps.storage.models import HandlingUnit, Pallet
from config.admin_site import wms_admin


class OpenAdmin(ModelAdmin):
    def has_module_permission(self, request):
        return True

    def has_view_permission(self, request, obj=None):
        return True

    def has_add_permission(self, request):
        return True

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return True


class ReadAdmin(OpenAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class UserAdmin(OpenAdmin):
    list_display = ("name", "login", "role", "is_active")
    fields = ("name", "login", "role", "is_active")


def _show(model, fields):
    admin = type(f"{model.__name__}Admin", (OpenAdmin,), {"list_display": fields})
    wms_admin.register(model, admin)


wms_admin.register(User, UserAdmin)
wms_admin.register(AdminRoleGrant, type("RoleGrantAdmin", (OpenAdmin,), {"list_display": ("role",)}))
wms_admin.register(AdminUserGrant, type("UserGrantAdmin", (OpenAdmin,), {"list_display": ("user",)}))

for model, fields in (
    (ProductGroup, ("name",)),
    (Product, ("code", "name", "group")),
    (Caliber, ("name", "sort_order")),
    (Grade, ("name", "sort_order")),
    (Packaging, ("name", "tare_kg")),
    (Supplier, ("name",)),
    (Client, ("name",)),
    (Seller, ("code", "name")),
    (Printer, ("name", "printer_type", "host", "is_default")),
    (Order, ("number", "client", "seller", "ship_date", "status")),
    (OrderLine, ("order", "product", "qty_kg")),
    (PurchaseOrder, ("number", "supplier", "doc_date", "status")),
    (PurchaseOrderLine, ("order", "product", "qty_kg")),
    (Task, ("number", "kind", "status")),
    (TaskLine, ("task", "product", "qty_kg")),
    (Receipt, ("number", "supplier", "doc_date", "status")),
    (Shipment, ("number", "client_name", "order")),
    (Pallet, ("number", "kind", "status")),
    (HandlingUnit, ("barcode", "status", "net_kg")),
):
    _show(model, fields)

wms_admin.register(Movement, type("MovementAdmin", (ReadAdmin,), {"list_display": ("pallet", "kind", "lot", "qty_kg")}))
wms_admin.register(Balance, type("BalanceAdmin", (ReadAdmin,), {"list_display": ("lot", "free_kg", "reserved_kg", "picked_kg")}))
