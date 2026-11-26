#!/usr/bin/env python3
"""Private Radiotap decoder for TestAX210; standard-library only.

Decodes Radiotap metadata and clear MAC headers, not application payloads.
Unsupported Radiotap fields fail explicitly rather than shifting later fields.
This module has no file-output or radio-control entry point.
"""
import collections
import json
import struct
import zlib

LAYOUT = {0:(8,8),1:(1,1),2:(1,1),3:(2,4),4:(2,2),5:(1,1),6:(1,1),
          7:(2,2),8:(2,2),9:(2,2),10:(1,1),11:(1,1),12:(1,1),13:(1,1),
          14:(2,2),15:(2,2),16:(1,1),17:(1,1),18:(4,8),19:(1,3),20:(4,8),
          21:(2,12),22:(8,12),23:(2,12),24:(2,12),26:(1,1),27:(2,4)}
NAMES = {0:'tsft',1:'flags',2:'legacy_rate',3:'channel',5:'signal_dbm',6:'noise_dbm',
         10:'tx_power_dbm',11:'antenna',14:'rx_flags',19:'ht_mcs',20:'ampdu',
         21:'vht',22:'timestamp',23:'he',24:'he_mu',26:'zero_length_psdu',27:'lsig'}


def radiotap(packet):
    if len(packet)<8 or packet[0]!=0:
        raise ValueError('Not a Radiotap v0 record')
    length=struct.unpack_from('<H',packet,2)[0]
    if not 8<=length<=len(packet):
        raise ValueError('Radiotap length outside packet')
    words=[];off=4
    while True:
        if off+4>length: raise ValueError('Truncated presence bitmap')
        word=struct.unpack_from('<I',packet,off)[0];off+=4;words.append(word)
        if not word & (1<<31): break
    fields=collections.defaultdict(list);base=0;namespace=0
    for word in words:
        for bit in range(29):
            if not word & (1<<bit): continue
            index=base+bit
            if index not in LAYOUT: raise ValueError(f'Unsupported Radiotap field {index}')
            align,size=LAYOUT[index];off=(off+align-1)//align*align
            if off+size>length: raise ValueError('Field extends past Radiotap header')
            fields[index].append((namespace,packet[off:off+size]));off+=size
        if word & (1<<30): raise ValueError('Vendor namespace not supported')
        if word & (1<<29):base=0;namespace+=1
        else:base+=32
    if off!=length: raise ValueError(f'Unparsed Radiotap bytes: {length-off}')
    return length,fields


def decode(packet,profile):
    length,fields=radiotap(packet)
    f={k:v[0][1] for k,v in fields.items()}
    row={'profile_primary_mhz':profile['primary_mhz'], 'profile_width_mhz':profile['width_mhz'],
         'packet_bytes':len(packet), 'radiotap_bytes':length,
         'radiotap_fields':';'.join(NAMES.get(k,str(k)) for k in sorted(fields)),
         'phy':'unknown', 'rssi_dbm':struct.unpack('<b',f[5])[0] if 5 in f else None,
         'chain0_dbm':None,'chain1_dbm':None}
    chains={ns:struct.unpack('<b',v)[0] for ns,v in fields.get(5,[]) if ns>0}
    for ns,v in fields.get(11,[]):
        if ns in chains:row[f'chain{v[0]}_dbm']=chains[ns]
    flags=f.get(1,b'\0')[0]
    row.update(fcs_included=bool(flags&16),bad_fcs_flag=bool(flags&64),
               phy_only=26 in f,zero_psdu_type=f[26][0] if 26 in f else None)
    if 0 in f:row['tsft_us']=int.from_bytes(f[0],'little')
    if 22 in f:row['hardware_timestamp']=int.from_bytes(f[22][:8],'little')
    if 3 in f:
        row['reported_frequency_mhz'],row['channel_flags']=struct.unpack('<HH',f[3])
        row['frequency_mismatch']=row['reported_frequency_mhz']!=profile['primary_mhz']
    if 2 in f:row.update(phy='legacy',legacy_rate_mbps=f[2][0]/2)
    if 20 in f:row['ampdu_reference'],row['ampdu_flags']=struct.unpack('<IH',f[20][:6])
    if 21 in f:
        known,fl,bw=struct.unpack('<HBB',f[21][:4]);pairs=[]
        for value in f[21][4:8]:
            if value&15:pairs.append((value>>4,value&15))
        row.update(phy='VHT',vht_known=known,vht_mcs_nss=json.dumps(pairs))
        if len(pairs)==1:row.update(mcs=pairs[0][0] if pairs[0][0]!=15 else None,nss=pairs[0][1])
        if known&64:row['bandwidth_mhz']={0:20,1:40,4:80,11:160}.get(bw);row['vht_bw_code']=bw
        if known&4:row['gi_us']=0.4 if fl&4 else 0.8
        if known&32:row['beamformed']=bool(fl&32)
        if known&1:row['stbc']=bool(fl&1)
        row['coding_user0']='LDPC' if f[21][8]&1 else 'BCC'
    if 23 in f:
        d1,d2,d3,d4,d5,d6=struct.unpack('<6H',f[23])
        row.update(phy='HE',he_format=['SU','EXT_SU','MU','TRIG'][d1&3],
                   he_known1=d1,he_known2=d2,nsts=d6&15 or None)
        if d1&32:row['mcs']=(d3>>8)&15
        if d1&64:row['dcm']=bool(d3&4096)
        if d1&128:row['coding_user0']='LDPC' if d3&8192 else 'BCC'
        if d1&512:
            row['stbc']=bool(d3&32768)
            # NSTS equals NSS when STBC is known to be absent.
            if not row['stbc']:row['nss']=row['nsts']
        if d1&4:row['bss_color']=d3&63
        if d1&16:row['he_uplink']=bool(d3&128)
        if d1&16384:
            code=d5&15;row['he_bw_ru_code']=code
            if code<4:row['bandwidth_mhz']=[20,40,80,160][code]
            else:row['ru_tones']={4:26,5:52,6:106,7:242,8:484,9:996,10:1992}.get(code)
        if d2&2:row['gi_us']={0:0.8,1:1.6,2:3.2}.get((d5>>4)&3)
        if d2&16:row['beamformed']=bool(d5&16384)
    if 19 in f:
        known,fl,index=f[19];row['phy']='HT'
        if known&2:row['mcs']=index
        if known&1:row['bandwidth_mhz']=40 if fl&3==1 else 20
        if known&4:row['gi_us']=0.4 if fl&4 else 0.8
    if 27 in f:
        known,value=struct.unpack('<HH',f[27])
        if known&1:row['lsig_rate_code']=value&15
        if known&2:row['lsig_length']=value>>4
    if row['phy_only']:
        row['mac_kind']='PHY-only'
        return row
    mac=packet[length:]
    if row['fcs_included'] and len(mac)>=4:
        row['fcs_crc_matches']=(zlib.crc32(mac[:-4])&0xffffffff)==int.from_bytes(mac[-4:],'little')
        mac=mac[:-4]
    if len(mac)<10:
        row['mac_kind']='short_header';return row
    fc=int.from_bytes(mac[:2],'little');typ=(fc>>2)&3;sub=(fc>>4)&15
    row.update(mac_kind={0:'management',1:'control',2:'data',3:'extension'}[typ],
               subtype=sub,retry=bool(fc&2048),protected=bool(fc&16384),
               to_ds=bool(fc&256),from_ds=bool(fc&512),receiver=mac[4:10].hex(':'))
    if len(mac)>=16 and not(typ==1 and sub in (12,13)):row['transmitter']=mac[10:16].hex(':')
    if typ in (0,2) and len(mac)>=24:
        a3=mac[16:22].hex(':');row['sequence_number']=int.from_bytes(mac[22:24],'little')>>4
        ds=(fc>>8)&3
        row['bssid']=a3 if typ==0 or ds==0 else row['receiver'] if ds==1 else row['transmitter'] if ds==2 else None
        if typ==2 and sub&8:
            qos=24+(6 if ds==3 else 0)
            if len(mac)>=qos+2:row['qos_tid']=mac[qos]&15
    row['beacon']=typ==0 and sub==8
    row['probe_response']=typ==0 and sub==5
    return row









if __name__ == "__main__":
    raise SystemExit("Private helper. Run TestAX210/analyze_power.py instead.")
