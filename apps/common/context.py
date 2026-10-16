from django.contrib import messages

from apps.common.notices import notice_state

from apps.common.constants import (
    DOC_STATUS,
    PALLET_KIND,
    PALLET_STATUS,
    PALLET_TYPE,
    PURCHASE_STATUS,
    ROLE_LABELS,
    TASK_KIND,
    TASK_STATUS,
    UNIT_STATUS,
)


def wms(request):
    storage = messages.get_messages(request)
    flashes = []
    for message in storage:
        level = "error" if message.level >= messages.ERROR else "ok"
        text = str(message)
        if flashes and flashes[-1]["m"] == text and flashes[-1]["l"] == level:
            continue
        flashes.append({"m": text, "l": level})
    notices, unread = notice_state(request.user)
    return {
        "flashes": flashes,
        "notices": notices,
        "notice_unread": unread,
        "ROLE_LABELS": ROLE_LABELS,
        "UNIT_STATUS": UNIT_STATUS,
        "DOC_STATUS": DOC_STATUS,
        "PURCHASE_STATUS": PURCHASE_STATUS,
        "PALLET_STATUS": PALLET_STATUS,
        "PALLET_KIND": PALLET_KIND,
        "PALLET_TYPE": PALLET_TYPE,
        "TASK_KIND": TASK_KIND,
        "TASK_STATUS": TASK_STATUS,
    }
