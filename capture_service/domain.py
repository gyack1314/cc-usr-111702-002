"""解释检验报告目录与原页。"""
import json
def decode(raw,receipt):
    d=json.loads(raw.decode('utf-8'))
    if 'institution' in d:
        return [{'type':'report','id':d['report'],'version':d['revision'],
                 'payload':{'institution':d['institution'],'sample':d['sample'],
                            'pages':d['pages'],'items':d['items']}}]
    if 'page' in d:
        return [{'type':'page','id':d['report']+'/'+str(d['page']),'version':d['revision'],
                 'payload':{'sample':d['sample'],'image_sha256':d['image_sha256'],
                            'ocr_candidate':d['ocr_text']}}]
    raise ValueError('报告资料非法')
def assess(rows):
    reports=[r for r in rows if r['type']=='report'];pages={r['id']:r for r in rows if r['type']=='page'}
    reasons=[]
    for report in reports:
        for page in report['payload']['pages']:
            found=pages.get(report['id']+'/'+str(page))
            if not found:reasons.append('报告缺页:'+str(page))
            elif found['payload']['sample']!=report['payload']['sample']:reasons.append('样品身份不一致:'+str(page))
    return {'publishable':not reasons,'reasons':reasons,'report_count':len(reports)}
