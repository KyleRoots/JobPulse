# Recruiter to LinkedIn Tag Mappings

**Last Updated:** February 24, 2026

This file contains the master list of all recruiter name to LinkedIn tag mappings used in the XML job feed generation system. When a job is owned by one of these recruiters in Bullhorn, the system automatically assigns the corresponding LinkedIn tag to the `<assignedrecruiter>` field in the XML output.

## Current Mappings (40 Total)

| Recruiter Name | LinkedIn Tag | Notes |
|----------------|--------------|-------|
| Adam Gebara | #LI-AG1 | |
| Amanda Messina | #LI-AM1 | |
| Amanda Messina (Smith) | #LI-AM1 | Name variant |
| Austin Zachrich | #LI-AZ1 | |
| Bryan Chinzorig | #LI-BC1 | |
| Chris Carter | #LI-CC1 | |
| Christine Carter | #LI-CC1 | Name variant |
| Dan Sifer | #LI-DS1 | |
| Daniel Sifer | #LI-DS1 | Name variant |
| Dawn Geistert-Dixon | #LI-DG1 | |
| Dominic Scaletta | #LI-DS2 | |
| Jayne Kritschgau | #LI-JK1 | |
| Julie Johnson | #LI-JJ1 | |
| Kaniz Abedin | #LI-KA1 | |
| Kyle Roots | #LI-KR1 | |
| **Kellie Miller** | **#LI-KM1** | **Added Jan 29, 2026** |
| **Lisa Keirsted** | **#LI-DS1** | **Added Nov 5, 2025** |
| Lisa Mattis-Keirsted | #LI-LM1 | |
| Maddie Lewis | #LI-ML1 | |
| Madhu Sinha | #LI-MS1 | |
| Matheo Theodossiou | #LI-MT1 | |
| Michael Billiu | #LI-MB1 | |
| Michael Theodossiou | #LI-MT2 | |
| Michelle Corino | #LI-MC1 | |
| Mike Gebara | #LI-MG1 | |
| Mike Scalzitti | #LI-MS2 | |
| Myticas Recruiter | #LI-RS1 | |
| Nick Theodossiou | #LI-NT1 | |
| Rachel Mann | #LI-RM1 | |
| Rachelle Fite | #LI-RF1 | |
| Reena Setya | #LI-RS2 | |
| Runa Parmar | #LI-RP1 | |
| Ryan Green | #LI-RG1 | |
| Ryan Oliver | #LI-RO1 | |
| Sarah Ferris | #LI-SF1 | |
| Sarah Ferris CSP | #LI-SF1 | Name variant |
| Shikha Gurung | #LI-SG1 | |
| Tarra Dziurman | #LI-TD1 | |
| **Tray Prewitt** | **#LI-TP1** | **Added Feb 24, 2026** |

## XML Output Format

When applied to jobs, the LinkedIn tag appears in the XML as:

```xml
<assignedrecruiter>
<![CDATA[ #LI-DS1 ]]>
</assignedrecruiter>
```

## How It Works

1. The system pulls job data from Bullhorn via the API
2. For each job, it checks the owner's name against this mapping list
3. If a match is found, the corresponding LinkedIn tag is added to the XML
4. Multiple name variants (e.g., "Dan Sifer" and "Daniel Sifer") map to the same tag
5. Jobs without a matching recruiter will have an empty `<assignedrecruiter>` field

## Adding New Mappings

To add a new recruiter mapping:

1. Edit `seed_database.py`
2. Add the new tuple to the `recruiter_mappings` list (line ~530)
3. Format: `('Recruiter Name', '#LI-XXX'),`
4. Redeploy the application
5. The next XML generation cycle will apply the new mapping
6. Update this file with the new mapping for reference

## Recent Changes

- **February 24, 2026**: Added "Tray Prewitt" → "#LI-TP1" mapping for new team member
- **January 29, 2026**: Added "Kellie Miller" → "#LI-KM1" mapping for new team member (kmiller@stsigroup.com)
- **November 5, 2025**: Added "Lisa Keirsted" → "#LI-DS1" mapping to assign Dan Sifer's LinkedIn tag to jobs owned by Lisa Keirsted in Bullhorn (Jobs 34522, 34523)
