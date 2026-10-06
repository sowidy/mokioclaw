import re, io

s = io.open('yoimiya_profile.html', encoding='utf-8').read()
t = s.lower()
assert '<html' in t and '</html>' in t, 'missing html tags'
assert '<title' in t, 'missing title'
assert '宵宫' in s, 'missing character name'
assert '火' in s and ('弓' in s) and '稻妻' in s, 'missing element/weapon/region'
links = sorted(set(re.findall(r'https?://[^\s\"<>)]+', s)))
assert len(links) >= 2, 'need >= 2 source links, got %d' % len(links)
assert any('wikipedia.org' in l or 'fandom.com' in l or 'mihoyo.com' in l for l in links), 'need a reputable source'
print('OK links=%d' % len(links))
[print(l) for l in links]
