from functools import wraps

from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import redirect

from apps.common.exceptions import WarehouseError


def accept(get=None, post=None):
    def view(request, *args, **kwargs):
        handler = post if request.method == "POST" else get
        if handler is None:
            return HttpResponseNotAllowed(["POST"] if post else ["GET"])
        return handler(request, *args, **kwargs)

    return view


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            user = request.user
            path = request.path
            login_url = "/tsd/login" if path.startswith("/tsd") else "/login"
            if not user.is_authenticated or not user.is_active:
                return redirect(login_url)
            if user.role != "admin":
                allowed = set(roles)
                if "office" not in allowed:
                    allowed.add("office")
                if user.role not in allowed:
                    messages.error(request, "Недостаточно прав")
                    return redirect("/tsd" if path.startswith("/tsd") else "/")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def guarded(request, back, fn):
    try:
        with transaction.atomic():
            return fn(), None
    except WarehouseError as exc:
        messages.error(request, str(exc))
        return None, redirect(back)
    except (IntegrityError, ProtectedError):
        messages.error(request, "Запись уже есть или используется")
        return None, redirect(back)


def done(request, url, message):
    messages.success(request, message)
    return redirect(url)


def back_url(request, fallback: str) -> str:
    return request.META.get("HTTP_REFERER") or fallback
