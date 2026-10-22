"""Retired private adapters are deliberately not redistributed."""
def apply(*args, **kwargs):
    raise RuntimeError("旧通用插件补丁已退役；请使用固定指纹的公开适配，未知版本保留原生")

def restore(*args, **kwargs):
    return {"restored": 0, "skipped": 0, "unsafe": 1,
            "message": "该历史私有账本不能由公开版自动处理，请使用原安装版本恢复"}
