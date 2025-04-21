from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'BB-run-regular-updates-every-hour': {
        'task': 'aa_bb.tasks.BB_run_regular_updates',
        'schedule': crontab(minute=0, hour='*'),  # Every hour on the hour
    },
}
