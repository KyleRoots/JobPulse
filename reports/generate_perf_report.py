"""Scout Genius monthly performance report renderer.

Two ways to use:

  1. As a library (preferred for scheduled monthly reports):
       from reports.generate_perf_report import render_report
       render_report(metrics_dict, output_path='/tmp/may_2026.png')

  2. As a standalone script (ad-hoc / on-demand):
       uv run python reports/generate_perf_report.py
     — uses the DEFAULT_METRICS dict below for quick visual checks.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec


DEFAULT_METRICS = {
    'period_label': 'April 2026 – May 2026',
    'resumes_total': 7949,
    'resumes_completed': 7908,
    'qualified': 1187,
    'recommended_unique': 1411,
    'notifications': 1492,
    'inbound_parsed': 6251,
    'inbound_landed': 6176,
    'avg_sec': 57.9,
    'median_sec': 52.1,
    'p95_sec': 111.8,
    'placements': 8,
    'monthly_breakdown': [
        {'label': 'April 2026', 'screened': 1361, 'qualified': 255},
        {'label': 'May 2026', 'screened': 6588, 'qualified': 932},
    ],
    'human_min_per_resume': 15,
    'recruiter_hourly': 50,
}


def render_report(metrics: dict, output_path: str) -> str:
    """Render the dashboard PNG from a metrics dict and save to output_path.

    Returns the output_path. The metrics dict must conform to DEFAULT_METRICS shape.
    """
    m = {**DEFAULT_METRICS, **metrics}

    resumes_completed = m['resumes_completed']
    avg_sec = m['avg_sec']
    human_min = m['human_min_per_resume']
    hourly = m['recruiter_hourly']

    ai_hours = (resumes_completed * avg_sec) / 3600
    human_hours = (resumes_completed * human_min) / 60
    saved_hours = human_hours - ai_hours
    saved_cost = (human_hours - ai_hours) * hourly
    speedup = (human_min * 60) / avg_sec if avg_sec > 0 else 0

    fig = plt.figure(figsize=(16, 11), facecolor='#0d1117')
    gs = GridSpec(3, 3, figure=fig, hspace=0.55, wspace=0.35,
                  left=0.06, right=0.97, top=0.92, bottom=0.06)

    fig.suptitle(
        f'Scout Genius — Inbound & Screening Performance Report\n{m["period_label"]}',
        fontsize=20, fontweight='bold', color='#f0f6fc', y=0.97
    )

    TILE_BG = '#161b22'
    ACCENT = '#58a6ff'
    GREEN = '#3fb950'
    ORANGE = '#f0883e'
    TEXT = '#f0f6fc'
    SUB = '#8b949e'

    def kpi_tile(ax, value, label, sub, color=ACCENT):
        ax.set_facecolor(TILE_BG)
        ax.text(0.5, 0.62, value, ha='center', va='center',
                fontsize=36, fontweight='bold', color=color, transform=ax.transAxes)
        ax.text(0.5, 0.30, label, ha='center', va='center',
                fontsize=12, color=TEXT, fontweight='bold', transform=ax.transAxes)
        ax.text(0.5, 0.13, sub, ha='center', va='center',
                fontsize=9, color=SUB, transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_color('#30363d')

    rec_rate = (m['placements'] / m['recommended_unique'] * 100) if m['recommended_unique'] else 0
    completion_rate = (resumes_completed / m['resumes_total'] * 100) if m['resumes_total'] else 0

    ax1 = fig.add_subplot(gs[0, 0])
    kpi_tile(ax1, f'{resumes_completed:,}', 'Resumes Screened',
             f'{m["resumes_total"]:,} initiated · {completion_rate:.1f}% completion', ACCENT)
    ax2 = fig.add_subplot(gs[0, 1])
    kpi_tile(ax2, f'{m["recommended_unique"]:,}', 'Candidates Recommended',
             f'{m["notifications"]:,} recruiter notifications sent', GREEN)
    ax3 = fig.add_subplot(gs[0, 2])
    kpi_tile(ax3, f'{m["placements"]}', 'Confirmed Placements',
             f'{rec_rate:.2f}% recommend→hire rate', ORANGE)

    ax4 = fig.add_subplot(gs[1, 0])
    ax4.set_facecolor(TILE_BG)
    months = [b['label'] for b in m['monthly_breakdown']]
    screened = [b['screened'] for b in m['monthly_breakdown']]
    qualified = [b['qualified'] for b in m['monthly_breakdown']]
    x = list(range(len(months)))
    ax4.bar([i - 0.18 for i in x], screened, width=0.36, color=ACCENT, label='Screened')
    ax4.bar([i + 0.18 for i in x], qualified, width=0.36, color=GREEN, label='Qualified')
    ax4.set_xticks(x); ax4.set_xticklabels(months, color=TEXT, fontsize=9)
    ax4.tick_params(colors=SUB, labelsize=8)
    ax4.set_title('Monthly Throughput', color=TEXT, fontsize=11, fontweight='bold', pad=8)
    ax4.legend(facecolor=TILE_BG, edgecolor='#30363d', labelcolor=TEXT, fontsize=8, loc='upper left')
    for s in ax4.spines.values():
        s.set_color('#30363d')
    if screened:
        for i, v in enumerate(screened):
            ax4.text(i - 0.18, v + max(screened) * 0.02, f'{v:,}', ha='center',
                     color=ACCENT, fontsize=8, fontweight='bold')
        for i, v in enumerate(qualified):
            ax4.text(i + 0.18, v + max(screened) * 0.02, f'{v:,}', ha='center',
                     color=GREEN, fontsize=8, fontweight='bold')
        ax4.set_ylim(0, max(screened) * 1.18)

    ax5 = fig.add_subplot(gs[1, 1])
    ax5.set_facecolor(TILE_BG)
    labels = ['AI (Scout)', f'Human (@{human_min}min/resume)']
    hours = [ai_hours, human_hours]
    bars = ax5.barh(labels, hours, color=[ACCENT, '#8b949e'])
    ax5.set_title('Total Screening Time — AI vs. Human', color=TEXT, fontsize=11, fontweight='bold', pad=8)
    ax5.tick_params(colors=SUB, labelsize=9)
    for s in ax5.spines.values():
        s.set_color('#30363d')
    for bar, h in zip(bars, hours):
        ax5.text(bar.get_width() + max(hours) * 0.01, bar.get_y() + bar.get_height() / 2,
                 f'{h:,.0f} hrs', va='center', color=TEXT, fontsize=10, fontweight='bold')
    ax5.set_xlim(0, max(hours) * 1.18 if hours else 1)
    ax5.set_xlabel('Hours', color=SUB, fontsize=9)

    ax6 = fig.add_subplot(gs[1, 2])
    ax6.set_facecolor(TILE_BG)
    metrics_labels = ['Median', 'Average', 'P95 (slowest 5%)']
    seconds = [m['median_sec'], m['avg_sec'], m['p95_sec']]
    ax6.bar(metrics_labels, seconds, color=[GREEN, ACCENT, ORANGE])
    ax6.set_title('Per-Resume Screening Time (seconds)', color=TEXT, fontsize=11, fontweight='bold', pad=8)
    ax6.tick_params(colors=SUB, labelsize=9)
    for s in ax6.spines.values():
        s.set_color('#30363d')
    for i, v in enumerate(seconds):
        ax6.text(i, v + max(seconds) * 0.02, f'{v:.1f}s', ha='center',
                 color=TEXT, fontsize=10, fontweight='bold')
    ax6.set_ylim(0, max(seconds) * 1.18 if seconds else 1)
    ax6.set_ylabel('Seconds', color=SUB, fontsize=9)

    ax7 = fig.add_subplot(gs[2, :])
    ax7.set_facecolor(TILE_BG)
    ax7.set_xticks([]); ax7.set_yticks([])
    for s in ax7.spines.values():
        s.set_color('#30363d')

    ax7.text(0.5, 0.92, 'Cost & Time Savings vs. Manual Screening',
             ha='center', va='center', fontsize=14, fontweight='bold',
             color=TEXT, transform=ax7.transAxes)

    ax7.text(0.165, 0.62, f'{saved_hours:,.0f}', ha='center', fontsize=34,
             fontweight='bold', color=GREEN, transform=ax7.transAxes)
    ax7.text(0.165, 0.40, 'Recruiter Hours Saved', ha='center', fontsize=11,
             color=TEXT, fontweight='bold', transform=ax7.transAxes)
    ax7.text(0.165, 0.28, f'{saved_hours/40:.0f} full work-weeks reclaimed',
             ha='center', fontsize=9, color=SUB, transform=ax7.transAxes)

    ax7.text(0.5, 0.62, f'${saved_cost:,.0f}', ha='center', fontsize=34,
             fontweight='bold', color=ORANGE, transform=ax7.transAxes)
    ax7.text(0.5, 0.40, 'Loaded Labor Cost Avoided', ha='center', fontsize=11,
             color=TEXT, fontweight='bold', transform=ax7.transAxes)
    ax7.text(0.5, 0.28,
             f'Recruiter blended rate: ${hourly}/hr · {human_min}min/resume baseline',
             ha='center', fontsize=9, color=SUB, transform=ax7.transAxes)

    ax7.text(0.835, 0.62, f'{speedup:.0f}×', ha='center', fontsize=34,
             fontweight='bold', color=ACCENT, transform=ax7.transAxes)
    ax7.text(0.835, 0.40, 'Faster than Human', ha='center', fontsize=11,
             color=TEXT, fontweight='bold', transform=ax7.transAxes)
    ax7.text(0.835, 0.28,
             f'{avg_sec:.0f}s per resume vs. {human_min}min manual',
             ha='center', fontsize=9, color=SUB, transform=ax7.transAxes)

    ax7.text(0.5, 0.09,
             f'Calculation basis: {resumes_completed:,} completed screenings × '
             f'({human_min} min human − {avg_sec:.1f}s AI) at ${hourly}/hr loaded recruiter rate.',
             ha='center', fontsize=8, color=SUB, style='italic', transform=ax7.transAxes)

    plt.savefig(output_path, dpi=130, facecolor='#0d1117', bbox_inches='tight')
    plt.close(fig)
    return output_path


if __name__ == '__main__':
    out = render_report(DEFAULT_METRICS, 'reports/scout_perf_report_2026-05-19.png')
    print(f'Saved {out}')
