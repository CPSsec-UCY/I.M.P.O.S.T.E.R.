"""Open, vendor-labelled training profiles for configurable lab plants.

Profiles describe typical public device classes and nominal operating ranges.
They are not firmware images, engineering projects, or visual replicas.
"""

DEVICE_PROFILES = [
    {"vendor": "Siemens", "model": "S7-1200 training profile", "type": "PLC", "rated_kw": 5, "nominal_v": 24, "pf": 0.92, "range": "18-30 VDC", "family": "Control"},
    {"vendor": "Siemens", "model": "S7-1500 training profile", "type": "PLC", "rated_kw": 12, "nominal_v": 24, "pf": 0.93, "range": "18-30 VDC", "family": "Control"},
    {"vendor": "Siemens", "model": "SINAMICS G120 training profile", "type": "VFD", "rated_kw": 75, "nominal_v": 400, "pf": 0.88, "range": "0-50 Hz", "family": "Drives"},
    {"vendor": "Siemens", "model": "SITRANS flow training profile", "type": "Flow Meter", "rated_kw": 2, "nominal_v": 24, "pf": 0.95, "range": "0-1,000 m3/h", "family": "Instrumentation"},
    {"vendor": "ABB", "model": "ACS580 training profile", "type": "VFD", "rated_kw": 90, "nominal_v": 400, "pf": 0.89, "range": "0-50 Hz", "family": "Drives"},
    {"vendor": "ABB", "model": "AC500 training profile", "type": "PLC", "rated_kw": 8, "nominal_v": 24, "pf": 0.93, "range": "18-30 VDC", "family": "Control"},
    {"vendor": "ABB", "model": "REF615 training profile", "type": "Protection Relay", "rated_kw": 1, "nominal_v": 110, "pf": 0.96, "range": "80-264 VAC", "family": "Protection"},
    {"vendor": "Schneider Electric", "model": "Modicon M221 training profile", "type": "PLC", "rated_kw": 5, "nominal_v": 24, "pf": 0.92, "range": "20-28 VDC", "family": "Control"},
    {"vendor": "Schneider Electric", "model": "Altivar 630 training profile", "type": "VFD", "rated_kw": 55, "nominal_v": 400, "pf": 0.87, "range": "0-50 Hz", "family": "Drives"},
    {"vendor": "Schneider Electric", "model": "PowerLogic meter training profile", "type": "Power Meter", "rated_kw": 1, "nominal_v": 400, "pf": 0.98, "range": "0-690 VAC", "family": "Metering"},
    {"vendor": "Rockwell Automation", "model": "CompactLogix training profile", "type": "PLC", "rated_kw": 7, "nominal_v": 24, "pf": 0.93, "range": "18-30 VDC", "family": "Control"},
    {"vendor": "Rockwell Automation", "model": "PowerFlex 755 training profile", "type": "VFD", "rated_kw": 110, "nominal_v": 480, "pf": 0.88, "range": "0-60 Hz", "family": "Drives"},
    {"vendor": "Danfoss", "model": "VLT AutomationDrive training profile", "type": "VFD", "rated_kw": 45, "nominal_v": 400, "pf": 0.87, "range": "0-50 Hz", "family": "Drives"},
    {"vendor": "Emerson", "model": "DeltaV controller training profile", "type": "DCS Controller", "rated_kw": 10, "nominal_v": 24, "pf": 0.93, "range": "18-30 VDC", "family": "Control"},
    {"vendor": "Honeywell", "model": "Experion controller training profile", "type": "DCS Controller", "rated_kw": 10, "nominal_v": 24, "pf": 0.93, "range": "18-30 VDC", "family": "Control"},
    {"vendor": "Yokogawa", "model": "STARDOM controller training profile", "type": "RTU", "rated_kw": 8, "nominal_v": 24, "pf": 0.94, "range": "18-30 VDC", "family": "Control"},
    {"vendor": "Endress+Hauser", "model": "Promag flow training profile", "type": "Flow Meter", "rated_kw": 2, "nominal_v": 24, "pf": 0.95, "range": "0-2,500 m3/h", "family": "Instrumentation"},
    {"vendor": "VEGA", "model": "VEGAPULS level training profile", "type": "Level Sensor", "rated_kw": 1, "nominal_v": 24, "pf": 0.96, "range": "0-30 m", "family": "Instrumentation"},
    {"vendor": "WIKA", "model": "Pressure transmitter training profile", "type": "Pressure Sensor", "rated_kw": 1, "nominal_v": 24, "pf": 0.96, "range": "0-250 bar", "family": "Instrumentation"},
    {"vendor": "Generic", "model": "Centrifugal process pump", "type": "Pump", "rated_kw": 55, "nominal_v": 400, "pf": 0.86, "range": "0-900 m3/h", "family": "Process"},
    {"vendor": "Generic", "model": "Grid-tied inverter", "type": "Inverter", "rated_kw": 250, "nominal_v": 400, "pf": 0.98, "range": "0-100% output", "family": "Power"},
    {"vendor": "Generic", "model": "Air compressor skid", "type": "Compressor", "rated_kw": 160, "nominal_v": 400, "pf": 0.85, "range": "0-10 bar", "family": "Process"},
]