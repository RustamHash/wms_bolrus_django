from django.db import models


class Notice(models.Model):
    user = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="notices")
    actor = models.CharField(max_length=120)
    text = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notices"
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        ordering = ["-id"]

    def __str__(self):
        return self.text


class NoticeCursor(models.Model):
    user = models.OneToOneField("accounts.User", on_delete=models.CASCADE, related_name="notice_cursor")
    seen_id = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "notice_cursors"
        verbose_name = "Просмотр уведомлений"
        verbose_name_plural = "Просмотры уведомлений"
