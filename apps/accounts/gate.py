import re

from django.contrib import messages
from django.shortcuts import redirect

from django.contrib.auth import logout

from apps.accounts.access import opens_tsd, opens_web, path_action, screen_allows, screens_for_path

_OPERATION = re.compile(r"^/(?:tasks/\d+/(?:receive|pick|ship)|movements/\d+/delete)$")


class StaffWebGate:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not opens_web(user):
            path = request.path
            if path.startswith("/tsd") or path.startswith("/static") or path == "/logout":
                return self.get_response(request)
            if path.startswith("/shift") and user.can_shift():
                return self.get_response(request)
            if request.method == "POST" and _OPERATION.match(path):
                return self.get_response(request)
            if opens_tsd(user):
                return redirect("/tsd")
            logout(request)
            messages.error(request, "У пользователя нет доступа в веб и на ТСД")
            return redirect("/login")
        return self.get_response(request)


class ScreenGate:
    """Меню веба и ТСД: чтение на GET, запись на POST, удаление на /delete."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)
        if user.is_superuser or user.role == "admin":
            return self.get_response(request)
        screens = screens_for_path(request.path)
        if not screens:
            return self.get_response(request)
        action = path_action(request.path, request.method)
        if action == "delete":
            allowed = ("delete",)
        elif action == "view":
            allowed = ("view",)
        else:
            allowed = ("change", "add")
        if any(screen_allows(user, screen, name) for screen in screens for name in allowed):
            return self.get_response(request)
        messages.error(request, "Недостаточно прав")
        return redirect("/tsd" if request.path.startswith("/tsd") else "/")
