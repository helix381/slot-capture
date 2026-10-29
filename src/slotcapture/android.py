"""Android inspection parsers shared by discovery and capture preflight."""
import re


PACKAGE = r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+'


class UIDResolutionError(RuntimeError):
    pass


def inspect(adb, command, trace=None):
    try:
        result = adb.run('shell', command, check=False)
        text = result.stdout.decode(errors='replace')
        error = result.stderr.decode(errors='replace')
        code = result.returncode
    except Exception as exc:
        text, error, code = '', str(exc), -1
    if trace is not None:
        trace.append(dict(command=command, returncode=code, stdout=text[:12000], stderr=error[:2000]))
    return code, text, error


def foreground_candidates(text):
    candidates = []
    for marker in ('topResumedActivity', 'mCurrentFocus', 'mResumedActivity', 'mFocusedApp'):
        for line in text.splitlines():
            if marker not in line: continue
            match = re.search(r'(?<![\w.])('+PACKAGE+r')/[A-Za-z0-9_.$]+', line)
            if not match: continue
            user = re.search(r'\bu(\d+)\b',line[:match.start()])
            item = dict(package=match.group(1), android_user=int(user.group(1)) if user else None, source=marker)
            if not any(c['package']==item['package'] and c['android_user']==item['android_user'] for c in candidates):
                candidates.append(item)
    return candidates


def package_uid_from_list(text, package, android_user=None):
    for line in text.splitlines():
        match = re.fullmatch(r'\s*package:('+PACKAGE+r')\s+uid\s*:\s*(\d+)\s*',line)
        if match and match.group(1)==package:
            uid=int(match.group(2))
            if android_user is None or uid//100000==android_user:
                return uid
    return None


def package_uid_from_dump(text, android_user=None):
    match=re.search(r'\b(?:userId|appId)\s*=\s*(\d+)\b',text)
    if not match: return None
    uid=int(match.group(1))
    if android_user is not None:
        if uid<100000: uid=android_user*100000+uid
        elif uid//100000!=android_user: return None
    return uid


def resolve_package_uid(adb, package, android_user=None, trace=None):
    if not re.fullmatch(PACKAGE,package):
        raise ValueError('检测到无效的应用包名：'+str(package))
    if android_user is not None and (isinstance(android_user,bool) or not isinstance(android_user,int) or not 0<=android_user<10000):
        raise ValueError('无效Android用户编号')
    code,text,error=inspect(adb,'dumpsys package '+package,trace)
    if code==0 and not re.search(r'(?:Unknown package|Unable to find package|Permission Denial)',text,re.I):
        uid=package_uid_from_dump(text,android_user)
        if uid is not None:return uid
    user_flag=f' --user {android_user}' if android_user is not None else ''
    for command in (f'cmd package list packages -U{user_flag} {package}',f'pm list packages -U{user_flag} {package}'):
        code,text,error=inspect(adb,command,trace)
        if code==0:
            uid=package_uid_from_list(text,package,android_user)
            if uid is not None:return uid
    raise UIDResolutionError(f'已识别前台包名 {package}，但未能取得UID（已尝试dumpsys、cmd package和pm）。')
