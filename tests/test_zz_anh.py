from test_my_work import people, purchase_people
from test_technician_stock import held, transfer, balance
from test_ux1b_hoan_thanh_gop import job
from test_web import service_login
def test_shot(client,db,job):
    p,_,t=job;t.kind=None;db.commit()
    service_login(client,p['technician'])
    html=client.get(f'/tho/phieu/{t.id}').text.replace('/static/','static/')
    open('/tmp/claude-0/anh/d0.html','w').write(html)
