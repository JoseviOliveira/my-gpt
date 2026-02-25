/**
 * admin_geo.js – ISO country codes, names, and normalization utilities for analytics.
 * Extracted from admin.js.
 */
(() => {
  'use strict';

  const REGION_OVERRIDES = {
    US: 'United States of America',
    GB: 'United Kingdom',
    AE: 'United Arab Emirates',
    TZ: 'United Republic of Tanzania',
    CN: 'China',
    LOCAL: 'Local network',
  };

  // Fallback names for browsers without Intl.DisplayNames support (derived from CLDR + ECharts map data).
  const ISO_NAME_FALLBACKS = {
    AE: 'United Arab Emirates',
    AF: 'Afghanistan',
    AL: 'Albania',
    AM: 'Armenia',
    AO: 'Angola',
    AQ: 'Antarctica',
    AR: 'Argentina',
    AT: 'Austria',
    AU: 'Australia',
    AZ: 'Azerbaijan',
    BA: 'Bosnia and Herzegovina',
    BD: 'Bangladesh',
    BE: 'Belgium',
    BF: 'Burkina Faso',
    BG: 'Bulgaria',
    BI: 'Burundi',
    BJ: 'Benin',
    BM: 'Bermuda',
    BN: 'Brunei',
    BO: 'Bolivia',
    BR: 'Brazil',
    BS: 'The Bahamas',
    BT: 'Bhutan',
    BW: 'Botswana',
    BY: 'Belarus',
    BZ: 'Belize',
    CA: 'Canada',
    CD: 'Democratic Republic of the Congo',
    CF: 'Central African Republic',
    CG: 'Republic of the Congo',
    CH: 'Switzerland',
    CI: 'Ivory Coast',
    CL: 'Chile',
    CM: 'Cameroon',
    CN: 'China',
    CO: 'Colombia',
    CR: 'Costa Rica',
    CU: 'Cuba',
    CY: 'Cyprus',
    CZ: 'Czech Republic',
    DE: 'Germany',
    DJ: 'Djibouti',
    DK: 'Denmark',
    DO: 'Dominican Republic',
    DZ: 'Algeria',
    EC: 'Ecuador',
    EE: 'Estonia',
    EG: 'Egypt',
    EH: 'Western Sahara',
    ER: 'Eritrea',
    ES: 'Spain',
    ET: 'Ethiopia',
    FI: 'Finland',
    FJ: 'Fiji',
    FK: 'Falkland Islands',
    FR: 'France',
    GA: 'Gabon',
    GB: 'United Kingdom',
    GE: 'Georgia',
    GF: 'French Guiana',
    GH: 'Ghana',
    GL: 'Greenland',
    GM: 'Gambia',
    GN: 'Guinea',
    GQ: 'Equatorial Guinea',
    GR: 'Greece',
    GT: 'Guatemala',
    GW: 'Guinea Bissau',
    GY: 'Guyana',
    HN: 'Honduras',
    HR: 'Croatia',
    HT: 'Haiti',
    HU: 'Hungary',
    ID: 'Indonesia',
    IE: 'Ireland',
    IL: 'Israel',
    IN: 'India',
    IQ: 'Iraq',
    IR: 'Iran',
    IS: 'Iceland',
    IT: 'Italy',
    JM: 'Jamaica',
    JO: 'Jordan',
    JP: 'Japan',
    KE: 'Kenya',
    KG: 'Kyrgyzstan',
    KH: 'Cambodia',
    KP: 'North Korea',
    KR: 'South Korea',
    KW: 'Kuwait',
    KZ: 'Kazakhstan',
    LA: 'Laos',
    LB: 'Lebanon',
    LK: 'Sri Lanka',
    LR: 'Liberia',
    LS: 'Lesotho',
    LT: 'Lithuania',
    LU: 'Luxembourg',
    LV: 'Latvia',
    LY: 'Libya',
    MA: 'Morocco',
    MD: 'Moldova',
    ME: 'Montenegro',
    MG: 'Madagascar',
    MK: 'Macedonia',
    ML: 'Mali',
    MM: 'Myanmar',
    MN: 'Mongolia',
    MR: 'Mauritania',
    MT: 'Malta',
    MW: 'Malawi',
    MX: 'Mexico',
    MY: 'Malaysia',
    MZ: 'Mozambique',
    NA: 'Namibia',
    NC: 'New Caledonia',
    NE: 'Niger',
    NG: 'Nigeria',
    NI: 'Nicaragua',
    NL: 'Netherlands',
    NO: 'Norway',
    NP: 'Nepal',
    NZ: 'New Zealand',
    OM: 'Oman',
    PA: 'Panama',
    PE: 'Peru',
    PG: 'Papua New Guinea',
    PH: 'Philippines',
    PK: 'Pakistan',
    PL: 'Poland',
    PR: 'Puerto Rico',
    PS: 'West Bank',
    PT: 'Portugal',
    PY: 'Paraguay',
    QA: 'Qatar',
    RO: 'Romania',
    RS: 'Republic of Serbia',
    RU: 'Russia',
    RW: 'Rwanda',
    SA: 'Saudi Arabia',
    SB: 'Solomon Islands',
    SD: 'Sudan',
    SE: 'Sweden',
    SI: 'Slovenia',
    SK: 'Slovakia',
    SL: 'Sierra Leone',
    SN: 'Senegal',
    SO: 'Somalia',
    SR: 'Suriname',
    SS: 'South Sudan',
    SV: 'El Salvador',
    SY: 'Syria',
    SZ: 'Swaziland',
    TD: 'Chad',
    TF: 'French Southern and Antarctic Lands',
    TG: 'Togo',
    TH: 'Thailand',
    TJ: 'Tajikistan',
    TL: 'East Timor',
    TM: 'Turkmenistan',
    TN: 'Tunisia',
    TR: 'Turkey',
    TT: 'Trinidad and Tobago',
    TW: 'Taiwan',
    TZ: 'United Republic of Tanzania',
    UA: 'Ukraine',
    UG: 'Uganda',
    US: 'United States of America',
    UY: 'Uruguay',
    UZ: 'Uzbekistan',
    VE: 'Venezuela',
    VN: 'Vietnam',
    VU: 'Vanuatu',
    YE: 'Yemen',
    ZA: 'South Africa',
    ZM: 'Zambia',
    ZW: 'Zimbabwe',
  };

  const regionNames = typeof Intl !== 'undefined' && Intl.DisplayNames
    ? new Intl.DisplayNames(['en'], { type: 'region' })
    : null;

  function isoToName(code) {
    if (!code) return '';
    const normalized = code.toUpperCase();
    if (REGION_OVERRIDES[normalized]) return REGION_OVERRIDES[normalized];
    if (regionNames) {
      try {
        const localized = regionNames.of(normalized);
        if (localized) return localized;
      } catch (err) {
        // Ignore Intl errors, fall back to static table.
      }
    }
    return ISO_NAME_FALLBACKS[normalized] || normalized;
  }

  function normalizeCountry(code) {
    const value = (code || '').toString().trim().toUpperCase();
    if (!value || value === 'LOCAL') return null;
    return value;
  }

  function formatCountry(code) {
    const name = isoToName(code);
    if (!name) return code || '—';
    return `${name}`;
  }

  window.DashboardGeo = {
    REGION_OVERRIDES,
    ISO_NAME_FALLBACKS,
    isoToName,
    normalizeCountry,
    formatCountry,
  };
})();
