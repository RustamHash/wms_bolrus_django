from django.contrib.admin import AdminSite


class WmsAdminSite(AdminSite):
    site_header = "Админка"
    site_title = "Админка"
    index_title = "Админка"

    def has_permission(self, request):
        user = request.user
        return bool(user.is_authenticated and user.is_active and user.opens_admin())


wms_admin = WmsAdminSite(name="wmsadmin")
