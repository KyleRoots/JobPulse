#!/usr/bin/env python3
"""
Fix job 34089 data issues:
1. Update truncated description with full content
2. Fix country from United States to Canada
"""

import logging
from lxml import etree
import os

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Full description for job 34089 (from the database snapshot)
FULL_DESCRIPTION = """<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><strong><span style="font-size: 10.5pt;">Must Haves:</span></strong></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;"><span style="font-family: Symbol;">·</span></span>       <span style="font-size: 10.0pt;">Minimum 7 years' experience as a solution Architect on Oracle Identity Access Management systems, 10G, 11G and 12C.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;"><span style="font-family: Symbol;">·</span></span>       <span style="font-size: 10.0pt;">Minimum 7 years solution development with 12C OIG (Oracle Identity Governance) who has successfully migrated user, service enrolment and organization data from 10G to 12C.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;"><span style="font-family: Symbol;">·</span></span>       <span style="font-size: 10.0pt;">Minimum 5 years in setting up 11G OIM and 12C OIG in multi-data center configuration and set-up.</span></span></span><br>
<br>
<br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><strong><span style="font-size: 10.0pt;">Background Information</span></strong></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">Ontario Health's ONEID service is a secure identity solution leveraged by the Ministry of Health and Long-Term Care and numerous health care organizations in Ontario for purposes of accessing patient health information (PHI). The ONEID service enhances protection of PHI and user account information through privacy and security safeguards while providing access to multiple digital health services using the same login credentials. </span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;"> The ONE ID service is based on Oracle Identity Access Management suite including Oracle Access Manager (OAM), Oracle Unified Directory (OUD), Oracle Identity Management (OIM), Oracle database, Microsoft Active Directory, and other Ontario Health custom systems. As such, Ontario Health requires Oracle Access Manager (OAM) and Oracle Unified Directory (OUD) experts to help resolve and navigate challenges in configuring and setting up the new 12C OAM and OUD to establish interoperability with the existing 10G based ONE ID and provide a smooth transition to the upgraded our current 10G/11G systems including Oracle Identity Manager (OIM) and Oracle Virtual Directory (OVD) to the full Oracle 12C IAM suite.  </span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">The purpose of this procurement is to procure one (1) Senior Identity Access Management Consultant required to perform the role of 12C <span style="background-color: white;"><span style="color: black;">Oracle Identity Management</span></span> (OIM) Data Migration – IAM Technical Consultant within a dedicated team for the ONEID Oracle 12c Upgrade.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><strong><span style="font-size: 10.0pt;">Must haves:</span></strong></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Minimum 7 years' experience as a solution Architect on Oracle Identity Access Management systems, 10G, 11G and 12C.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Minimum 7 years solution development with 12C OIG (Oracle Identity Governance) who has successfully migrated user, service enrolment and organization data from 10G to 12C.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Minimum 5 years in setting up 11G OIM and 12C OIG in multi-data center configuration and set-up.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Minimum 7 years integration experience in Oracle IAM suite including OAM, OIM, OUD/OVD, Oracle HTTP Server (OHS), Microsoft AD, and Oracle databases.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Over 5 years of experience in tuning Oracle IAM suites to work efficiently with high availability to work on WebLogic and Linux.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Experience on design and creation of service sand applications for enrollment in 12C OIG using native interface</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Experience in configuration of connectors and discounted resources for service/application enrollments in 12C OIG</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Must be associated with a recognized Gold Oracle IAM Partner.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Must be at expert level in Security Assertion Mark-up Language, SMAL 2.0, and OAuth 2.0.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Experience translating conceptual to logical to physical application architecture in alignment with business and architecture.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Able to articulate technical issues and provide options to resolve them clearly and concisely.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;"><span style="background-color: white;"><span style="color: black;">·      </span></span></span><span style="font-size: 10.0pt;">Able to produce clear and concise documentation including design/architecture documents, deployment and integration guides, and physical application design documents.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><strong><span style="font-size: 10.0pt;">Responsibilities:</span></strong></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Work with Ontario Health (OH) teams in design and configuration of 12C OIG in Multi-Data Centre (MDC) setup for high availability in upper and lower environments</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Work with Ontario Health (OH) teams to integrate 12C Phase 1 MDC system to existing 10G ONE ID for interoperability in environments for user and service transitions</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Work with Ontario Health (OH) teams to create and maintain service/application in 12C OIG</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Develop the flow for enrollment for each service/application</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Document the final design, installation, configuration, and integration procedures for all environments</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Work with Applications and Architects team to resolve the cross-domain and remaining 12C issues (such as the return URL) with Oracle and team.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Work collaboratively with other Ontario Health teams such as database, networking, and infrastructure.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Provide weekly updates to team leads and project manager.</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><strong><span style="font-size: 10.0pt;">Desired Skills:</span></strong></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Experience with Oracle and Identity and Access Management Suite Plus and Microsoft Active Directory Suite</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Knowledge of general IAM best practises</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Experience integrating business applications with Oracle IAM and Microsoft Active Directory Suite</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Experience triaging, analyzing, diagnosing (trouble-shooting), evaluating options, and resolving application problems, especially those related to identity and access management systems</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Experience with developing user identity, service creation and enrolments with Oracle Identity Manager (OIM) and Governance (OIG).</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Knowledge of IT security technologies particularly encryption and authentication technologies such as PKI, PKI, and TLS/SSL</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Excellent organizational skills, verbal and written communication skills, team working skills</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Experience with Oracle Identity Management data migration</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Experience in working with Agile development and Continuous Integration (CI)/ Continuous Development (CD) pipelines</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Knowledge of JIRA and Confluence</span></span></span><br>
<span style="font-size: 11.0pt;"><span style="font-family: Aptos , sans-serif;"><span style="font-size: 10.0pt;">·      Work collaboratively:</span></span></span>"""

def fix_job_34089():
    """Fix job 34089 data in both XML files"""
    xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    
    for xml_file in xml_files:
        logger.info(f"Processing {xml_file}...")
        
        # Parse XML with CDATA preservation
        parser = etree.XMLParser(strip_cdata=False, recover=True)
        tree = etree.parse(xml_file, parser)
        root = tree.getroot()
        
        # Find job 34089
        job_found = False
        for job in root.xpath('.//job'):
            bhatsid_elem = job.find('.//bhatsid')
            if bhatsid_elem is not None and bhatsid_elem.text and '34089' in bhatsid_elem.text.strip():
                job_found = True
                logger.info(f"Found job 34089 in {xml_file}")
                
                # Update description
                desc_elem = job.find('.//description')
                if desc_elem is not None:
                    desc_elem.clear()
                    desc_elem.text = f' {FULL_DESCRIPTION} '
                    logger.info("Updated description with full content")
                
                # Update country from United States to Canada
                country_elem = job.find('.//country')
                if country_elem is not None:
                    country_elem.clear()
                    country_elem.text = ' Canada '
                    logger.info("Updated country from United States to Canada")
                
                break
        
        if job_found:
            # Write the updated XML
            xml_content = etree.tostring(tree, encoding='unicode', pretty_print=True)
            
            with open(xml_file, 'w', encoding='utf-8') as f:
                f.write(xml_content)
            
            logger.info(f"Successfully updated job 34089 in {xml_file}")
        else:
            logger.warning(f"Job 34089 not found in {xml_file}")

def main():
    """Main function"""
    fix_job_34089()
    logger.info("Job 34089 fixes completed!")

if __name__ == "__main__":
    main()