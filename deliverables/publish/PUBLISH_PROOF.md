# Publish proof (dry run)

These files are produced by the same `build_calls()` the live path
uses, so they are the exact requests that would be sent -- not mocks.
Adding credentials to `.env` switches the adapter to live with no
code change.

| post | platform | status | calls | missing credentials |
|---|---|---|---|---|
| c02-instagram-A | instagram | dry_run | 4 | IG_USER_ID, IG_ACCESS_TOKEN |
| c02-tiktok-A | tiktok | dry_run | 4 | TIKTOK_ACCESS_TOKEN |
| c02-youtube-A | youtube | dry_run | 2 | YOUTUBE_ACCESS_TOKEN |
| c04-linkedin-A | linkedin | dry_run | 8 | LINKEDIN_ACCESS_TOKEN, LINKEDIN_ORG_URN |
| c04-linkedin-A-en | linkedin | dry_run | 8 | LINKEDIN_ACCESS_TOKEN, LINKEDIN_ORG_URN |
| c04-x-A | x | dry_run | 7 | X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET |
| c01a-instagram-A | instagram | dry_run | 4 | IG_USER_ID, IG_ACCESS_TOKEN |
| c01a-tiktok-A | tiktok | dry_run | 4 | TIKTOK_ACCESS_TOKEN |
| c01a-youtube-A | youtube | dry_run | 2 | YOUTUBE_ACCESS_TOKEN |
| c01b-instagram-B | instagram | dry_run | 4 | IG_USER_ID, IG_ACCESS_TOKEN |
| c01b-tiktok-B | tiktok | dry_run | 4 | TIKTOK_ACCESS_TOKEN |
| c01b-youtube-B | youtube | dry_run | 2 | YOUTUBE_ACCESS_TOKEN |
| c03-instagram-A | instagram | dry_run | 4 | IG_USER_ID, IG_ACCESS_TOKEN |
| c03-tiktok-A | tiktok | dry_run | 4 | TIKTOK_ACCESS_TOKEN |
| c03-youtube-A | youtube | dry_run | 2 | YOUTUBE_ACCESS_TOKEN |
| c05-linkedin-A | linkedin | dry_run | 13 | LINKEDIN_ACCESS_TOKEN, LINKEDIN_ORG_URN |
| c05-linkedin-A-en | linkedin | dry_run | 13 | LINKEDIN_ACCESS_TOKEN, LINKEDIN_ORG_URN |
| c05-x-A | x | dry_run | 11 | X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET |
