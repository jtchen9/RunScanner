"""Lab fronthaul BSSIDs. SSID and channel are learned from actual scans."""
ROWS = {
    # In order: 6 GHz, 5 GHz, 2.4 GHz
    'AP1': ('ac:91:9b:ff:f8:c1','ae:91:9b:ff:fa:c0','ae:91:9b:ff:fb:bf'),
    'AP2': ('ac:91:9b:ff:f8:d1','ae:91:9b:ff:fa:d0','ae:91:9b:ff:fb:cf'),
    'AP3': ('ac:91:9b:ff:f9:61','ae:91:9b:ff:fb:60','ae:91:9b:ff:fc:5f'),
    'AP4': ('b8:82:f2:45:f4:99','ba:82:f2:45:f6:98','ba:82:f2:45:f7:97'),
    'AP5': ('b8:82:f2:45:f3:a9','ba:82:f2:45:f5:a8','ba:82:f2:45:f6:a7'),
    'AP6': ('b8:82:f2:45:f5:b9','ba:82:f2:45:f7:b8','ba:82:f2:45:f8:b7'),
}
RADIOS = {b.upper(): (ap, band) for ap, radios in ROWS.items()
          for band, b in zip(('6G','5G','2.4G'), radios)}
MIN_RSSI_DBM = -80  # Starting policy; tune from measurements.
MAX_AGE_SEC = 90

SITE_APS = {
    'DemoRoom': ('AP1', 'AP4', 'AP5', 'AP6'),
    'Office': ('AP2',),
    'Home': ('AP3',),
}
ROBOT_SITE = {
    'alpha': 'DemoRoom', 'bravo': 'DemoRoom', 'charlie': 'DemoRoom', 'delta': 'DemoRoom',
    'echo': 'Office',
    'foxtrot': 'Home', 'golf': 'Home', 'hotel': 'Home', 'india': 'Home', 'julia': 'Home',
}


def radios_for(scanner):
    site = ROBOT_SITE.get(scanner.strip().lower().removeprefix('twin-scout-'))
    return frozenset(mac for mac, (ap, _) in RADIOS.items() if ap in SITE_APS.get(site, ()))
