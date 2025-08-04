from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import BigBrotherConfig
from .tasks import BB_register_message_tasks

@receiver(post_save, sender=BigBrotherConfig)
def trigger_task_sync(sender, instance, **kwargs):
    BB_register_message_tasks.delay()
