
# Add scheduled cleanup to scheduler
def schedule_file_cleanup():
    """Schedule automatic file cleanup"""
    with app.app_context():
        if hasattr(app, "file_consolidation"):
            try:
                results = app.file_consolidation.run_full_cleanup()
                app.logger.info(f"Scheduled file cleanup completed: {results.get(\"summary\", {})}")
            except Exception as e:
                app.logger.error(f"Scheduled file cleanup error: {e}")

# Add to scheduler after other jobs
scheduler.add_job(
    func=schedule_file_cleanup,
    trigger="interval", 
    hours=24,
    id="file_cleanup_job",
    name="Daily File Cleanup",
    replace_existing=True
)
app.logger.info("Scheduled daily file cleanup job")

