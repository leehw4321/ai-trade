"""Calendar observation periods and non-trading observation state."""
import calendar
import datetime as dt
import math

KST=dt.timezone(dt.timedelta(hours=9))
LIMITS={'hour':43800,'day':1825,'week':260,'month':60}


def period(value,unit):
    try:
        n=float(value)
        if not math.isfinite(n) or not n.is_integer() or unit not in LIMITS or not 1<=n<=LIMITS[unit]:raise ValueError()
        return {'value':int(n),'unit':unit}
    except (TypeError,ValueError):raise ValueError('관찰기간은 시간·일·주·월 단위의 양의 정수로 최대 5년까지 설정하세요.') from None


def until(start,p):
    if p['unit']!='month':return start+p['value']*{'hour':3600,'day':86400,'week':604800}[p['unit']]
    point=dt.datetime.fromtimestamp(start,KST)
    total=point.year*12+point.month-1+p['value'];year,month=divmod(total,12);month+=1
    return point.replace(year=year,month=month,day=min(point.day,calendar.monthrange(year,month)[1])).timestamp()
