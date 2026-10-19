"""Render only the selected run participant's private connection instructions."""
import io
from xml.sax.saxutils import escape


def participant_pdf(plan: dict, participant: dict, credentials: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    username = participant['username']
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('Credential', fontName='Courier', fontSize=9, leading=13, wordWrap='CJK'))
    story = [Paragraph('Range42 installation workshop', styles['Title']),
             Paragraph(escape(plan['namespace'] + ' / ' + username), styles['Heading1']),
             Paragraph(escape('Workshop ends: ' + plan['expires_at'] + ' (UTC)'), styles['Normal']),
             Spacer(1, 5 * mm)]
    rows = [('Participant', username),
            ('Your Proxmox', 'https://' + participant['proxmox_address'] + ':8006'),
            ('Proxmox user', 'root@pam'), ('Proxmox password', credentials['proxmox_password']),
            ('Your deployer SSH', 'ssh range42@' + participant['deployer_address']),
            ('Deployer password', credentials['deployer_password'])]
    table = Table([[Paragraph(escape(label), styles['Normal']), Paragraph(escape(value), styles['Credential'])]
                   for label, value in rows], colWidths=[48 * mm, 120 * mm], hAlign='LEFT')
    table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#edf2f7')),
        ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
    story += [table, Spacer(1, 5 * mm), Paragraph('Connect to your environment', styles['Heading2'])]
    for text in ['Keep this sheet private. These credentials belong only to your environment in this run.',
        'The facilitator must finish instance preparation and enable your laptop connection before you connect. '
        'Central application login and laptop network access are separate from these instance credentials.',
        'Obtain the authenticated Proxmox certificate and SSH fingerprints from the facilitator. '
        'Use the provided trust onboarding instructions before opening Proxmox.',
        'SSH to your deployer using the address and password above. Install Range42 during the workshop:',
        'range42-workshop prepare', 'range42-workshop install',
        'Use your own Proxmox address and root password in the installer. Choose catalog_try as the scenario, '
        'local for template storage, local-lvm for VM storage and vmbr0 as the guest bridge.',
        'After installation, reconnect SSH. Select your new context and deploy the practice lab, '
        'then test docker/_ctf/hello using range42-context catalog-try.',
        'To install your own Deployer UI after the CLI exercise, delete the inner practice VM with '
        'range42-context delete-vms, then run range42-workshop ui YOUR_CODENAME.',
        'Keep this SSH tunnel open on your laptop:',
        'ssh -L 3000:127.0.0.1:3000 -L 9013:127.0.0.1:9013 range42@' + participant['deployer_address'],
        'Open http://localhost:3000. Use http://localhost:9013 as the backend URL and the private API token '
        'generated on your own deployer. Keep that token private.',
        'Ask the facilitator for recovery if your environment or laptop connection fails. Other participant '
        'environments and platform management are outside your access.']:
        story += [Paragraph(escape(text), styles['Normal']), Spacer(1, 2 * mm)]
    output = io.BytesIO()
    SimpleDocTemplate(output, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title='Range42 workshop - ' + plan['namespace'] + ' - ' + username).build(story)
    return output.getvalue()
