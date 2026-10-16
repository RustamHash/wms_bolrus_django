from django.conf import settings
from django.urls import path

from apps.common.http import accept
from apps.accounts import views as accounts
from apps.catalog import views as catalog
from apps.inventory import views as inventory
from apps.picking import views as picking
from apps.planning import views as planning
from apps.printing import views as printing
from apps.receiving import views as receiving
from apps.shipping import views as shipping
from apps.storage import views as storage
from apps.tsd import views as tsd
from config.admin_site import wms_admin

import config.register_admin  # noqa: F401

urlpatterns = [
    path("", accounts.home),
    path("login", accept(accounts.login_form, accounts.login_submit)),
    path("logout", accept(post=accounts.logout_submit)),
    path("notices/seen", accept(post=accounts.notices_seen)),
    path("service", accounts.service_home),
    path("users", accept(accounts.users_list, accounts.users_create)),
    path("users/new", accounts.users_new),
    path("users/<int:user_id>/badge.png", accounts.users_badge_png),
    path("users/<int:user_id>/badge", accept(accounts.users_badge_page, accounts.users_new_badge)),
    path("users/<int:user_id>", accept(accounts.users_edit, accounts.users_update)),
    path("office", catalog.office_home),
    path("masters", catalog.masters_home),
    path("masters/consignee-zones/<int:consignee_id>", accept(post=catalog.consignee_zone_save)),
    path("masters/consignee-zones", catalog.consignee_zones),
    path("masters/consignee-grades/<int:consignee_id>", accept(post=catalog.consignee_grades_save)),
    path("masters/consignee-grades", catalog.consignee_grades),
    path("masters/<slug:kind>/new", catalog.master_new),
    path("masters/<slug:kind>/<int:item_id>/delete", accept(post=catalog.master_delete)),
    path("masters/<slug:kind>/<int:item_id>", accept(catalog.master_edit, catalog.master_update)),
    path("masters/<slug:kind>", accept(catalog.master_list, catalog.master_create)),
    path("orders", accept(planning.order_home, planning.order_create)),
    path("orders/suppliers/new", planning.supplier_order_new),
    path("orders/suppliers/import", accept(post=planning.supplier_orders_import)),
    path("orders/suppliers/tasks", accept(post=planning.supplier_orders_tasks)),
    path("orders/suppliers/revisions", planning.supplier_revision_report),
    path("orders/suppliers/<int:purchase_id>/revisions", planning.supplier_order_revisions),
    path("orders/suppliers/<int:purchase_id>/task", accept(post=planning.supplier_order_task)),
    path("orders/suppliers/<int:purchase_id>/fact", planning.supplier_order_fact),
    path("orders/suppliers/<int:purchase_id>", accept(planning.supplier_order_detail, planning.supplier_order_save)),
    path("orders/suppliers", accept(planning.supplier_orders, planning.supplier_order_create)),
    path("orders/customers/import", accept(post=planning.customer_orders_import)),
    path("orders/customers/tasks", accept(post=planning.customer_orders_tasks)),
    path("orders/customers/revisions", planning.order_revision_report),
    path("orders/customers", planning.customer_orders),
    path("orders/new", planning.order_new),
    path("orders/<int:order_id>/revisions", planning.order_revisions),
    path("orders/<int:order_id>/tasks", accept(post=planning.order_task)),
    path("orders/<int:order_id>", accept(planning.order_detail, planning.order_save)),
    path("tasks", accept(planning.task_list, planning.tasks_bulk)),
    path("tasks/<int:task_id>/status", accept(post=planning.task_status)),
    path("tasks/<int:task_id>/close", planning.task_close),
    path("tasks/<int:task_id>/close-order", accept(post=planning.task_close_order)),
    path("tasks/<int:task_id>/receive", receiving.task_receive),
    path("tasks/<int:task_id>/pick", picking.task_pick),
    path("tasks/<int:task_id>/ship", shipping.task_ship),
    path("tasks/<int:task_id>", planning.task_detail),
    path("reports", inventory.reports_home),
    path("warehouse", inventory.warehouse_home),
    path("stock", inventory.stock),
    path("demand", inventory.demand),
    path("pallets", accept(storage.pallet_list, storage.pallet_create)),
    path("pallets/<int:pallet_id>/location", storage.pallet_location),
    path("pallets/<int:pallet_id>/close", storage.pallet_close),
    path("pallets/<int:pallet_id>/units", storage.pallet_add),
    path("pallets/<int:pallet_id>", storage.pallet_detail),
    path("units/<int:unit_id>/detach", storage.unit_detach),
    path("units/<int:unit_id>/quality", storage.unit_quality),
    path("units/<int:unit_id>/reject", storage.unit_reject),
    path("units/<int:unit_id>", storage.unit_detail),
    path("receipts", receiving.receipts_redirect),
    path("receipts/<int:receipt_id>", receiving.receipts_redirect),
    path("movements/<int:movement_id>/delete", receiving.movement_delete),
    path("movements/<int:movement_id>", receiving.movement_edit),
    path("crossdock", shipping.crossdock_redirect),
    path("shipments", shipping.shipment_list),
    path("shipments/<int:shipment_id>", shipping.shipment_detail),
    path("print/units/<int:unit_id>.pdf", printing.unit_pdf),
    path("print/units/<int:unit_id>", printing.unit_print),
    path("print/pallets/<int:pallet_id>.pdf", printing.pallet_pdf),
    path("print/pallets/<int:pallet_id>", printing.pallet_print),
    path("print/shipments/<int:shipment_id>/sticker.pdf", printing.sticker_pdf),
    path("print/shipments/<int:shipment_id>/waybill.pdf", printing.waybill_pdf),
    path("print/shipments/<int:shipment_id>", printing.shipment_print),
    path("print/printers/<int:printer_id>/test", printing.printer_test),
    path("tsd/login", accept(accounts.tsd_login_form, accounts.tsd_login_submit)),
    path("tsd", tsd.tsd_menu),
    path("tsd/receive", tsd.tsd_receive),
    path("tsd/pick", tsd.tsd_pick),
    path("tsd/ship", tsd.tsd_ship),
    path("tsd/tasks/<int:task_id>", tsd.tsd_task),
    path("tsd/find", tsd.tsd_find),
    path("admin/", wms_admin.urls),
]

if settings.DEBUG:
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns += debug_toolbar_urls()
