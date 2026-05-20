import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec

RESUMES_TOTAL = 7949
RESUMES_COMPLETED = 7908
QUALIFIED = 1187
RECOMMENDED_UNIQUE = 1411
NOTIFICATIONS = 1492
INBOUND_PARSED = 6251
INBOUND_LANDED = 6176
AVG_SEC = 57.9
MEDIAN_SEC = 52.1
P95_SEC = 111.8
PLACEMENTS = 8

HUMAN_MIN_PER_RESUME = 15
RECRUITER_HOURLY = 50

ai_hours = (RESUMES_COMPLETED * AVG_SEC) / 3600
human_hours = (RESUMES_COMPLETED * HUMAN_MIN_PER_RESUME) / 60
saved_hours = human_hours - ai_hours
ai_cost = ai_hours * RECRUITER_HOURLY * 0
human_cost = human_hours * RECRUITER_HOURLY
saved_cost = human_cost - (ai_hours * RECRUITER_HOURLY)

fig = plt.figure(figsize=(16, 11), facecolor='#0d1117')
gs = GridSpec(3, 3, figure=fig, hspace=0.55, wspace=0.35,
              left=0.06, right=0.97, top=0.92, bottom=0.06)

fig.suptitle('Scout Genius — Inbound & Screening Performance Report\nApril 2026 – May 2026',
             fontsize=20, fontweight='bold', color='#f0f6fc', y=0.97)

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
    for s in ax.spines.values(): s.set_color('#30363d')

ax1 = fig.add_subplot(gs[0, 0]); kpi_tile(ax1, f'{RESUMES_COMPLETED:,}', 'Resumes Screened', f'{RESUMES_TOTAL:,} initiated · {RESUMES_COMPLETED/RESUMES_TOTAL*100:.1f}% completion', ACCENT)
ax2 = fig.add_subplot(gs[0, 1]); kpi_tile(ax2, f'{RECOMMENDED_UNIQUE:,}', 'Candidates Recommended', f'{NOTIFICATIONS:,} recruiter notifications sent', GREEN)
ax3 = fig.add_subplot(gs[0, 2]); kpi_tile(ax3, f'{PLACEMENTS}', 'Confirmed Placements', f'{PLACEMENTS/RECOMMENDED_UNIQUE*100:.2f}% recommend→hire rate', ORANGE)

ax4 = fig.add_subplot(gs[1, 0])
ax4.set_facecolor(TILE_BG)
months = ['April 2026', 'May 2026']
screened = [1361, 6588]
qualified = [255, 932]
x = range(len(months))
ax4.bar([i - 0.18 for i in x], screened, width=0.36, color=ACCENT, label='Screened')
ax4.bar([i + 0.18 for i in x], qualified, width=0.36, color=GREEN, label='Qualified')
ax4.set_xticks(list(x)); ax4.set_xticklabels(months, color=TEXT, fontsize=9)
ax4.tick_params(colors=SUB, labelsize=8)
ax4.set_title('Monthly Throughput', color=TEXT, fontsize=11, fontweight='bold', pad=8)
ax4.legend(facecolor=TILE_BG, edgecolor='#30363d', labelcolor=TEXT, fontsize=8, loc='upper left')
for s in ax4.spines.values(): s.set_color('#30363d')
for i, v in enumerate(screened): ax4.text(i - 0.18, v + 100, f'{v:,}', ha='center', color=ACCENT, fontsize=8, fontweight='bold')
for i, v in enumerate(qualified): ax4.text(i + 0.18, v + 100, f'{v:,}', ha='center', color=GREEN, fontsize=8, fontweight='bold')
ax4.set_ylim(0, max(screened) * 1.18)

ax5 = fig.add_subplot(gs[1, 1])
ax5.set_facecolor(TILE_BG)
labels = ['AI (Scout)', f'Human (@{HUMAN_MIN_PER_RESUME}min/resume)']
hours = [ai_hours, human_hours]
bars = ax5.barh(labels, hours, color=[ACCENT, '#8b949e'])
ax5.set_title('Total Screening Time — AI vs. Human', color=TEXT, fontsize=11, fontweight='bold', pad=8)
ax5.tick_params(colors=SUB, labelsize=9)
for s in ax5.spines.values(): s.set_color('#30363d')
for bar, h in zip(bars, hours):
    ax5.text(bar.get_width() + max(hours)*0.01, bar.get_y() + bar.get_height()/2,
             f'{h:,.0f} hrs', va='center', color=TEXT, fontsize=10, fontweight='bold')
ax5.set_xlim(0, max(hours) * 1.18)
ax5.set_xlabel('Hours', color=SUB, fontsize=9)

ax6 = fig.add_subplot(gs[1, 2])
ax6.set_facecolor(TILE_BG)
metrics = ['Median', 'Average', 'P95 (slowest 5%)']
seconds = [MEDIAN_SEC, AVG_SEC, P95_SEC]
ax6.bar(metrics, seconds, color=[GREEN, ACCENT, ORANGE])
ax6.set_title('Per-Resume Screening Time (seconds)', color=TEXT, fontsize=11, fontweight='bold', pad=8)
ax6.tick_params(colors=SUB, labelsize=9)
for s in ax6.spines.values(): s.set_color('#30363d')
for i, v in enumerate(seconds): ax6.text(i, v + 3, f'{v:.1f}s', ha='center', color=TEXT, fontsize=10, fontweight='bold')
ax6.set_ylim(0, max(seconds) * 1.18)
ax6.set_ylabel('Seconds', color=SUB, fontsize=9)

ax7 = fig.add_subplot(gs[2, :])
ax7.set_facecolor(TILE_BG)
ax7.set_xticks([]); ax7.set_yticks([])
for s in ax7.spines.values(): s.set_color('#30363d')

ax7.text(0.5, 0.92, 'Cost & Time Savings vs. Manual Screening',
         ha='center', va='center', fontsize=14, fontweight='bold', color=TEXT, transform=ax7.transAxes)

ax7.text(0.165, 0.62, f'{saved_hours:,.0f}', ha='center', fontsize=34, fontweight='bold', color=GREEN, transform=ax7.transAxes)
ax7.text(0.165, 0.40, 'Recruiter Hours Saved', ha='center', fontsize=11, color=TEXT, fontweight='bold', transform=ax7.transAxes)
ax7.text(0.165, 0.28, f'{saved_hours/40:.0f} full work-weeks reclaimed', ha='center', fontsize=9, color=SUB, transform=ax7.transAxes)

ax7.text(0.5, 0.62, f'${saved_cost:,.0f}', ha='center', fontsize=34, fontweight='bold', color=ORANGE, transform=ax7.transAxes)
ax7.text(0.5, 0.40, 'Loaded Labor Cost Avoided', ha='center', fontsize=11, color=TEXT, fontweight='bold', transform=ax7.transAxes)
ax7.text(0.5, 0.28, f'Recruiter blended rate: ${RECRUITER_HOURLY}/hr · {HUMAN_MIN_PER_RESUME}min/resume baseline', ha='center', fontsize=9, color=SUB, transform=ax7.transAxes)

speedup = (HUMAN_MIN_PER_RESUME * 60) / AVG_SEC
ax7.text(0.835, 0.62, f'{speedup:.0f}×', ha='center', fontsize=34, fontweight='bold', color=ACCENT, transform=ax7.transAxes)
ax7.text(0.835, 0.40, 'Faster than Human', ha='center', fontsize=11, color=TEXT, fontweight='bold', transform=ax7.transAxes)
ax7.text(0.835, 0.28, f'{AVG_SEC:.0f}s per resume vs. {HUMAN_MIN_PER_RESUME}min manual', ha='center', fontsize=9, color=SUB, transform=ax7.transAxes)

ax7.text(0.5, 0.09,
         f'Calculation basis: {RESUMES_COMPLETED:,} completed screenings × ({HUMAN_MIN_PER_RESUME} min human − {AVG_SEC:.1f}s AI) at ${RECRUITER_HOURLY}/hr loaded recruiter rate.',
         ha='center', fontsize=8, color=SUB, style='italic', transform=ax7.transAxes)

plt.savefig('reports/scout_perf_report_2026-05-19.png', dpi=130, facecolor='#0d1117', bbox_inches='tight')
print('Saved reports/scout_perf_report_2026-05-19.png')
print(f'Saved hours: {saved_hours:.0f} | Saved cost: ${saved_cost:,.0f} | Speedup: {speedup:.0f}x')
