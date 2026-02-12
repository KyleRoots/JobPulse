"""
Embedding Digest Service for JobPulse

Generates and sends daily digest emails summarizing:
- Embedding filter stats (Layer 1)
- Escalation effectiveness stats (Layer 3)
- Top borderline filtered pairs

Scheduled to run at 8 AM ET daily, sending to kroots@myticas.com.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple


DIGEST_RECIPIENT = 'kroots@myticas.com'

# Estimated cost per GPT-4o call (Layer 2 was previously GPT-4o)
ESTIMATED_GPT4O_COST_PER_CALL = 0.03
# Estimated cost per GPT-4o-mini call (current Layer 2)
ESTIMATED_GPT4O_MINI_COST_PER_CALL = 0.003
# Estimated cost per embedding call
ESTIMATED_EMBEDDING_COST_PER_CALL = 0.00002


def get_digest_data(since: datetime = None) -> Dict:
    """
    Aggregate embedding filter and escalation stats for the digest.
    
    Args:
        since: Start of the period (defaults to 24 hours ago)
        
    Returns:
        Dictionary with all digest data sections
    """
    from app import db
    from models import EmbeddingFilterLog, EscalationLog
    
    if since is None:
        since = datetime.utcnow() - timedelta(hours=24)
    
    # ═══════════════════════════════════════════
    # SECTION 1: Embedding Filter Stats
    # ═══════════════════════════════════════════
    filter_logs = EmbeddingFilterLog.query.filter(
        EmbeddingFilterLog.filtered_at >= since
    ).all()
    
    total_filtered = len(filter_logs)
    
    # Get total pairs evaluated (filtered + passed through)
    # We can estimate this from vetting logs in the same period
    from models import CandidateJobMatch
    total_passed = CandidateJobMatch.query.filter(
        CandidateJobMatch.created_at >= since
    ).count()
    
    total_evaluated = total_filtered + total_passed
    filter_rate = (total_filtered / total_evaluated * 100) if total_evaluated > 0 else 0
    
    # Cost savings: each filtered pair avoided a GPT-4o-mini call
    # Savings = filtered_count * (gpt4o_mini_cost - embedding_cost)
    savings_per_pair = ESTIMATED_GPT4O_MINI_COST_PER_CALL - ESTIMATED_EMBEDDING_COST_PER_CALL
    daily_savings = total_filtered * savings_per_pair
    
    # Bonus savings from model switch (GPT-4o → GPT-4o-mini for passed pairs)
    model_switch_savings = total_passed * (ESTIMATED_GPT4O_COST_PER_CALL - ESTIMATED_GPT4O_MINI_COST_PER_CALL)
    total_daily_savings = daily_savings + model_switch_savings
    
    # ═══════════════════════════════════════════
    # SECTION 2: Top Borderline Filtered Pairs
    # ═══════════════════════════════════════════
    borderline_logs = EmbeddingFilterLog.query.filter(
        EmbeddingFilterLog.filtered_at >= since,
        EmbeddingFilterLog.similarity_score >= 0.20,
        EmbeddingFilterLog.similarity_score <= 0.30
    ).order_by(EmbeddingFilterLog.similarity_score.desc()).limit(20).all()
    
    borderline_pairs = [{
        'candidate_name': log.candidate_name or 'Unknown',
        'job_title': log.job_title or 'Unknown',
        'similarity_score': round(log.similarity_score, 4),
        'filtered_at': log.filtered_at
    } for log in borderline_logs]
    
    # ═══════════════════════════════════════════
    # SECTION 3: Escalation Effectiveness Stats
    # ═══════════════════════════════════════════
    escalation_logs = EscalationLog.query.filter(
        EscalationLog.escalated_at >= since
    ).all()
    
    total_escalated = len(escalation_logs)
    material_changes = sum(1 for log in escalation_logs if log.material_change)
    threshold_crossings = sum(1 for log in escalation_logs if log.crossed_threshold)
    
    # Score band breakdown
    band_60_69 = [log for log in escalation_logs if 60 <= log.mini_score < 70]
    band_70_79 = [log for log in escalation_logs if 70 <= log.mini_score < 80]
    band_80_85 = [log for log in escalation_logs if 80 <= log.mini_score <= 85]
    
    def band_stats(logs):
        if not logs:
            return {'count': 0, 'material': 0, 'crossed': 0, 'avg_delta': 0.0}
        return {
            'count': len(logs),
            'material': sum(1 for l in logs if l.material_change),
            'crossed': sum(1 for l in logs if l.crossed_threshold),
            'avg_delta': round(sum(l.score_delta for l in logs) / len(logs), 1)
        }
    
    return {
        'period_start': since,
        'period_end': datetime.utcnow(),
        # Section 1
        'total_evaluated': total_evaluated,
        'total_filtered': total_filtered,
        'total_passed': total_passed,
        'filter_rate': round(filter_rate, 1),
        'daily_savings': round(total_daily_savings, 2),
        'filter_savings': round(daily_savings, 2),
        'model_savings': round(model_switch_savings, 2),
        # Section 2
        'borderline_pairs': borderline_pairs,
        'borderline_count': len(borderline_pairs),
        # Section 3
        'total_escalated': total_escalated,
        'material_changes': material_changes,
        'threshold_crossings': threshold_crossings,
        'band_60_69': band_stats(band_60_69),
        'band_70_79': band_stats(band_70_79),
        'band_80_85': band_stats(band_80_85),
    }


def build_digest_html(data: Dict) -> str:
    """
    Build the HTML email body for the daily digest.
    
    Args:
        data: Digest data dictionary from get_digest_data()
        
    Returns:
        HTML string for the email body
    """
    period_start = data['period_start'].strftime('%b %d, %Y %H:%M UTC')
    period_end = data['period_end'].strftime('%b %d, %Y %H:%M UTC')
    
    # Build borderline pairs rows
    borderline_rows = ''
    if data['borderline_pairs']:
        for i, pair in enumerate(data['borderline_pairs'], 1):
            score = pair['similarity_score']
            # Amber highlight for very borderline (0.23-0.30)
            row_color = '#fff3cd' if score >= 0.23 else '#f8f9fa'
            borderline_rows += f"""
            <tr style="background-color: {row_color};">
                <td style="padding: 8px; border: 1px solid #dee2e6;">{i}</td>
                <td style="padding: 8px; border: 1px solid #dee2e6;">{pair['candidate_name']}</td>
                <td style="padding: 8px; border: 1px solid #dee2e6;">{pair['job_title']}</td>
                <td style="padding: 8px; border: 1px solid #dee2e6; text-align: center; font-weight: bold;">{score:.4f}</td>
            </tr>"""
    else:
        borderline_rows = '<tr><td colspan="4" style="padding: 12px; text-align: center; color: #6c757d;">No borderline filtered pairs in this period</td></tr>'
    
    # Build escalation band rows
    def band_row(label, band):
        if band['count'] == 0:
            return f'<tr><td style="padding: 8px; border: 1px solid #dee2e6;">{label}</td><td colspan="4" style="padding: 8px; border: 1px solid #dee2e6; text-align: center; color: #6c757d;">—</td></tr>'
        crossed_color = '#dc3545' if band['crossed'] > 0 else '#28a745'
        return f"""
        <tr>
            <td style="padding: 8px; border: 1px solid #dee2e6; font-weight: bold;">{label}</td>
            <td style="padding: 8px; border: 1px solid #dee2e6; text-align: center;">{band['count']}</td>
            <td style="padding: 8px; border: 1px solid #dee2e6; text-align: center;">{band['material']}</td>
            <td style="padding: 8px; border: 1px solid #dee2e6; text-align: center; color: {crossed_color}; font-weight: bold;">{band['crossed']}</td>
            <td style="padding: 8px; border: 1px solid #dee2e6; text-align: center;">{band['avg_delta']:+.1f}</td>
        </tr>"""
    
    # Savings color
    savings_color = '#28a745' if data['daily_savings'] > 0 else '#6c757d'

    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; max-width: 700px; margin: 0 auto; color: #333;">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #1a1a2e, #16213e); padding: 24px; border-radius: 8px 8px 0 0;">
            <h1 style="color: #e94560; margin: 0; font-size: 22px;">🔍 Embedding Filter Daily Digest</h1>
            <p style="color: #a0a0b0; margin: 8px 0 0; font-size: 13px;">{period_start} → {period_end}</p>
        </div>
        
        <!-- Summary Cards -->
        <div style="background: #f8f9fa; padding: 20px; border: 1px solid #dee2e6; display: flex; justify-content: space-between;">
            <div style="text-align: center; flex: 1; padding: 0 8px;">
                <div style="font-size: 28px; font-weight: bold; color: #1a1a2e;">{data['total_evaluated']}</div>
                <div style="font-size: 12px; color: #6c757d; text-transform: uppercase;">Pairs Evaluated</div>
            </div>
            <div style="text-align: center; flex: 1; padding: 0 8px; border-left: 1px solid #dee2e6; border-right: 1px solid #dee2e6;">
                <div style="font-size: 28px; font-weight: bold; color: #e94560;">{data['total_filtered']}</div>
                <div style="font-size: 12px; color: #6c757d; text-transform: uppercase;">Filtered Out</div>
            </div>
            <div style="text-align: center; flex: 1; padding: 0 8px; border-right: 1px solid #dee2e6;">
                <div style="font-size: 28px; font-weight: bold; color: #0f3460;">{data['filter_rate']}%</div>
                <div style="font-size: 12px; color: #6c757d; text-transform: uppercase;">Filter Rate</div>
            </div>
            <div style="text-align: center; flex: 1; padding: 0 8px;">
                <div style="font-size: 28px; font-weight: bold; color: {savings_color};">${data['daily_savings']:.2f}</div>
                <div style="font-size: 12px; color: #6c757d; text-transform: uppercase;">Est. Savings</div>
            </div>
        </div>
        
        <!-- Section 1: Filter Stats -->
        <div style="padding: 20px; border: 1px solid #dee2e6; border-top: none;">
            <h2 style="font-size: 16px; color: #1a1a2e; margin: 0 0 12px;">📊 Embedding Filter Stats</h2>
            <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                <tr>
                    <td style="padding: 6px 0; color: #6c757d;">Total pairs evaluated</td>
                    <td style="padding: 6px 0; text-align: right; font-weight: bold;">{data['total_evaluated']}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #6c757d;">Filtered by embedding (saved GPT calls)</td>
                    <td style="padding: 6px 0; text-align: right; font-weight: bold; color: #e94560;">{data['total_filtered']}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #6c757d;">Passed to GPT analysis</td>
                    <td style="padding: 6px 0; text-align: right; font-weight: bold;">{data['total_passed']}</td>
                </tr>
                <tr style="border-top: 1px solid #dee2e6;">
                    <td style="padding: 6px 0; color: #6c757d;">Filter rate</td>
                    <td style="padding: 6px 0; text-align: right; font-weight: bold;">{data['filter_rate']}%</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #6c757d;">Savings from filter (avoided GPT-4o-mini calls)</td>
                    <td style="padding: 6px 0; text-align: right; color: #28a745;">${data['filter_savings']:.2f}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #6c757d;">Savings from model switch (GPT-4o → mini)</td>
                    <td style="padding: 6px 0; text-align: right; color: #28a745;">${data['model_savings']:.2f}</td>
                </tr>
                <tr style="border-top: 2px solid #1a1a2e;">
                    <td style="padding: 8px 0; font-weight: bold;">Total estimated daily savings</td>
                    <td style="padding: 8px 0; text-align: right; font-weight: bold; font-size: 18px; color: {savings_color};">${data['daily_savings']:.2f}</td>
                </tr>
            </table>
        </div>
        
        <!-- Section 2: Borderline Filtered Pairs -->
        <div style="padding: 20px; border: 1px solid #dee2e6; border-top: none;">
            <h2 style="font-size: 16px; color: #1a1a2e; margin: 0 0 4px;">⚠️ Top Borderline Filtered Pairs</h2>
            <p style="font-size: 12px; color: #6c757d; margin: 0 0 12px;">
                Showing {data['borderline_count']} pairs with similarity 0.20–0.30 (closest to threshold). 
                Review these to validate the threshold is correct.
            </p>
            <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                <thead>
                    <tr style="background-color: #1a1a2e; color: white;">
                        <th style="padding: 8px; border: 1px solid #dee2e6; width: 30px;">#</th>
                        <th style="padding: 8px; border: 1px solid #dee2e6;">Candidate</th>
                        <th style="padding: 8px; border: 1px solid #dee2e6;">Job Title</th>
                        <th style="padding: 8px; border: 1px solid #dee2e6; width: 80px;">Similarity</th>
                    </tr>
                </thead>
                <tbody>
                    {borderline_rows}
                </tbody>
            </table>
        </div>
        
        <!-- Section 3: Escalation Effectiveness -->
        <div style="padding: 20px; border: 1px solid #dee2e6; border-top: none; border-radius: 0 0 8px 8px;">
            <h2 style="font-size: 16px; color: #1a1a2e; margin: 0 0 12px;">📈 Escalation Effectiveness (Layer 3)</h2>
            
            <div style="display: flex; gap: 16px; margin-bottom: 16px;">
                <div style="flex: 1; background: #e8f4fd; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="font-size: 24px; font-weight: bold; color: #0f3460;">{data['total_escalated']}</div>
                    <div style="font-size: 11px; color: #6c757d;">Total Escalated</div>
                </div>
                <div style="flex: 1; background: #fff3cd; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="font-size: 24px; font-weight: bold; color: #856404;">{data['material_changes']}</div>
                    <div style="font-size: 11px; color: #6c757d;">Material Changes (±5pts)</div>
                </div>
                <div style="flex: 1; background: {'#f8d7da' if data['threshold_crossings'] > 0 else '#d4edda'}; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="font-size: 24px; font-weight: bold; color: {'#dc3545' if data['threshold_crossings'] > 0 else '#28a745'};">{data['threshold_crossings']}</div>
                    <div style="font-size: 11px; color: #6c757d;">Threshold Crossings</div>
                </div>
            </div>
            
            <h3 style="font-size: 14px; color: #6c757d; margin: 0 0 8px;">Breakdown by Score Band</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                <thead>
                    <tr style="background-color: #1a1a2e; color: white;">
                        <th style="padding: 8px; border: 1px solid #dee2e6;">Band</th>
                        <th style="padding: 8px; border: 1px solid #dee2e6;">Count</th>
                        <th style="padding: 8px; border: 1px solid #dee2e6;">Material</th>
                        <th style="padding: 8px; border: 1px solid #dee2e6;">Crossed</th>
                        <th style="padding: 8px; border: 1px solid #dee2e6;">Avg Δ</th>
                    </tr>
                </thead>
                <tbody>
                    {band_row('60–69%', data['band_60_69'])}
                    {band_row('70–79%', data['band_70_79'])}
                    {band_row('80–85%', data['band_80_85'])}
                </tbody>
            </table>
            
            <p style="font-size: 11px; color: #a0a0b0; margin: 16px 0 0; text-align: center;">
                JobPulse Embedding Filter Monitor • 14-Day Review Period • 
                <a href="#" style="color: #0f3460;">View Full Audit</a>
            </p>
        </div>
    </div>
    """
    
    return html


def send_daily_digest(since: datetime = None) -> bool:
    """
    Generate and send the daily embedding filter digest email.
    
    Args:
        since: Start of the period (defaults to 24 hours ago)
        
    Returns:
        True if email was sent successfully
    """
    try:
        data = get_digest_data(since=since)
        html = build_digest_html(data)
        
        subject = (
            f"🔍 Embedding Filter Digest — "
            f"{data['total_filtered']} filtered, "
            f"{data['total_escalated']} escalated, "
            f"${data['daily_savings']:.2f} saved"
        )
        
        from email_service import EmailService
        email = EmailService()
        
        success = email.send_html_email(
            to_email=DIGEST_RECIPIENT,
            subject=subject,
            html_content=html,
            notification_type='embedding_digest'
        )
        
        if success:
            logging.info(f"✅ Daily embedding digest sent to {DIGEST_RECIPIENT}")
        else:
            logging.error(f"❌ Failed to send daily embedding digest to {DIGEST_RECIPIENT}")
        
        return success
        
    except Exception as e:
        logging.error(f"Error generating/sending daily digest: {str(e)}")
        return False
