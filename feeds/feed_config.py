"""
XML feed configuration — tearsheet sets, filenames, and apply URL source params.

v2 (myticas-job-feed-v2.xml): Myticas sponsored tearsheets + STSI LinkedIn (1531).
Channel feeds: STSI Indeed (1640) and ZipRecruiter (1641) only.
"""

# Bullhorn tearsheet IDs
TEARSHEET_OTT = 1231
TEARSHEET_CHI = 1232
TEARSHEET_CLE = 1233
TEARSHEET_VMS = 1239
TEARSHEET_GR = 1474
TEARSHEET_STSI_LINKEDIN = 1531
TEARSHEET_STSI_INDEED = 1640
TEARSHEET_STSI_ZIPRECRUITER = 1641

V2_TEARSHEET_IDS = [
    TEARSHEET_OTT,
    TEARSHEET_CHI,
    TEARSHEET_CLE,
    TEARSHEET_VMS,
    TEARSHEET_GR,
    TEARSHEET_STSI_LINKEDIN,
]

TEARSHEET_MONITOR_MAPPING = {
    TEARSHEET_OTT: 'Sponsored - OTT',
    TEARSHEET_CHI: 'Sponsored - CHI',
    TEARSHEET_CLE: 'Sponsored - CLE',
    TEARSHEET_VMS: 'Sponsored - VMS',
    TEARSHEET_GR: 'Sponsored - GR',
    TEARSHEET_STSI_LINKEDIN: 'Sponsored - STSI - LinkedIn',
    TEARSHEET_STSI_INDEED: 'Sponsored - STSI - Indeed',
    TEARSHEET_STSI_ZIPRECRUITER: 'Sponsored - STSI - Zip Recruiter',
}

# Apply URL ?source= values (normalized by source_attribution.py on submit)
SOURCE_LINKEDIN = 'LinkedIn'
SOURCE_INDEED = 'Indeed'
SOURCE_ZIPRECRUITER = 'ZipRecruiter'

V2_FILENAME = 'myticas-job-feed-v2.xml'
V2_FILENAME_DEV = 'myticas-job-feed-v2-dev.xml'
STSI_INDEED_FILENAME = 'stsi-job-feed-indeed.xml'
STSI_INDEED_FILENAME_DEV = 'stsi-job-feed-indeed-dev.xml'
STSI_ZIPRECRUITER_FILENAME = 'stsi-job-feed-ziprecruiter.xml'
STSI_ZIPRECRUITER_FILENAME_DEV = 'stsi-job-feed-ziprecruiter-dev.xml'

CHANNEL_FEEDS = (
    {
        'key': 'stsi_indeed',
        'tearsheet_ids': [TEARSHEET_STSI_INDEED],
        'source_channel': SOURCE_INDEED,
        'filename': STSI_INDEED_FILENAME,
        'filename_dev': STSI_INDEED_FILENAME_DEV,
        'allow_empty': True,
    },
    {
        'key': 'stsi_ziprecruiter',
        'tearsheet_ids': [TEARSHEET_STSI_ZIPRECRUITER],
        'source_channel': SOURCE_ZIPRECRUITER,
        'filename': STSI_ZIPRECRUITER_FILENAME,
        'filename_dev': STSI_ZIPRECRUITER_FILENAME_DEV,
        'allow_empty': True,
    },
)
