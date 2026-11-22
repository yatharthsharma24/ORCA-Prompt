---
id: K-DHW
title: Degree Heating Weeks and coral bleaching alert levels
source: NOAA Coral Reef Watch, "Methodology, Product Description" (5 km daily products).
url: https://coralreefwatch.noaa.gov/product/5km/methodology.php
---
NOAA Coral Reef Watch (CRW) measures accumulated heat stress on coral reefs with Degree Heating Weeks (DHW). A HotSpot is the amount by which sea surface temperature exceeds the maximum monthly mean (MMM) climatology for a location. DHW sums HotSpots of at least 1 degC over a rolling 12-week window, so the unit is degC-weeks.

CRW bleaching alert levels: Bleaching Alert Level 1 starts at DHW of 4 degC-weeks, where significant coral bleaching is likely. Bleaching Alert Level 2 starts at DHW of 8 degC-weeks, where severe bleaching and significant coral mortality are likely. In December 2023 CRW added Alert Levels 3 to 5 at 12, 16 and 20 degC-weeks for the more extreme heat stress seen in recent events.

NOAA confirmed the fourth global coral bleaching event in April 2024; the third global event ran from 2014 to 2017. Indian reefs in Lakshadweep, the Andaman Islands and the Gulf of Mannar bleached in the strong warm years of 1998, 2010 and 2016.

The max_dhw column in the ORCA demo table coral_reef_monitoring is a monthly approximation of the CRW method (monthly HotSpots of at least 1 degC summed over three months and converted to weeks). It reproduces the idea, not the CRW product.
