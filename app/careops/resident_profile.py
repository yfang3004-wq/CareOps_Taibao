"""老人主档的可选输入校验；不核验真实身份、不生成临床结论。"""
import re

NEW_FIELDS = ('sex','age','id_number','birthplace','household_location','former_occupation','care_level','dementia_status','guardian_name','guardian_phone')
PRIVATE_FIELDS = {'id_number','household_location','guardian_name','guardian_phone'}
LIMITS = {'sex':20,'id_number':18,'birthplace':120,'household_location':200,'former_occupation':120,'care_level':120,'dementia_status':20,'guardian_name':80,'guardian_phone':40}


def validate_profile(row):
    for key in NEW_FIELDS:
        value = row.get(key)
        if value is None or value == '': continue
        if key == 'age':
            if isinstance(value, bool) or not isinstance(value,(int,str)) or not re.fullmatch(r'\d{1,3}',str(value)) or not 0 <= int(value) <= 130:
                raise ValueError('年龄须为0至130整数；未知可留空')
            continue
        if not isinstance(value,str) or len(value) > LIMITS[key]: raise ValueError('老人档案字段格式或长度错误：'+key)
        if re.search(r'[\x00-\x1f\x7f]',value): raise ValueError('老人档案字段含控制字符')
        if key == 'sex' and value not in {'男','女','其他','待确认'}: raise ValueError('性别选项不正确')
        if key == 'dementia_status' and value not in {'是','否','待确认'}: raise ValueError('认知症状态仅登记已确认信息或待确认')
        if key == 'id_number' and not re.fullmatch(r'(?:\d{15}|\d{17}[\dXx])',value): raise ValueError('身份证号仅接受15位数字或18位数字/X格式；不验证真实身份')
        if key == 'guardian_phone' and (not re.fullmatch(r'\+?[0-9][0-9 ()\-xX#转]{3,38}[0-9]',value) or not 5 <= len(re.sub(r'\D','',value)) <= 20):
            raise ValueError('监护人电话格式错误；支持座机、国际区号及分机，未知可留空')
