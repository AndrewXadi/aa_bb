from django.apps import AppConfig
from django.db.utils import OperationalError, ProgrammingError

class AaBbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aa_bb"
    verbose_name = "aa_bb"

    def ready(self):
        import logging
        logger = logging.getLogger(__name__)
        logger.info("🟢 aa_bb.AaBbConfig.ready() fired!")

        try:
            from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule

            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=1,
                period=IntervalSchedule.HOURS,
            )

            task, created = PeriodicTask.objects.get_or_create(
                name="BB run regular updates",
                defaults={
                    "interval": schedule,
                    "task": "aa_bb.tasks.BB_run_regular_updates",
                    "enabled": False,  # only on creation
                },
            )

            if not created:
                updated = False
                if task.interval != schedule or task.task != "aa_bb.tasks.BB_run_regular_updates":
                    task.interval = schedule
                    task.task = "aa_bb.tasks.BB_run_regular_updates"
                    task.save()
                    updated = True
                if updated:
                    logger.info("✅ Updated ‘BB run regular updates’ periodic task")
                else:
                    logger.info("ℹ️ ‘BB run regular updates’ periodic task already exists and is up to date")
            else:
                logger.info("✅ Created ‘BB run regular updates’ periodic task with enabled=False")
            # Schedule: every day at 1 PM
            daily_schedule, _ = CrontabSchedule.objects.get_or_create(
                minute='0',
                hour='12',
                day_of_week='*',
                day_of_month='*',
                month_of_year='*',
                timezone='UTC'  # Adjust if you're using another timezone
            )

            daily_task, created = PeriodicTask.objects.get_or_create(
                name="BB send daily message",
                defaults={
                    "crontab": daily_schedule,
                    "task": "aa_bb.tasks.BB_send_daily_messages",
                    "enabled": True,
                },
            )

            if not created:
                updated = False
                if daily_task.crontab != daily_schedule or daily_task.task != "aa_bb.tasks.BB_send_daily_messages":
                    daily_task.crontab = daily_schedule
                    daily_task.task = "aa_bb.tasks.BB_send_daily_messages"
                    daily_task.save()
                    updated = True
                if updated:
                    logger.info("✅ Updated ‘BB send daily message’ periodic task")
                else:
                    logger.info("ℹ️ ‘BB send daily message’ periodic task already exists and is up to date")
            else:
                logger.info("✅ Created ‘BB send daily message’ periodic task with enabled=True")
        except (OperationalError, ProgrammingError) as e:
            logger.warning(f"Could not register periodic task yet: {e}")



