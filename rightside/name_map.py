"""leaders.sw2 名称 -> test1 环境快照 sw_name 人工修正映射。

test2 龙头 CSV 的申万二级行业名称与 test1 官方名称大多一致，
对不上的条目在此登记（键 = leaders.sw2 原文，值 = env CSV 的 sw_name）。
每次全量运行如出现"名称对不上"清单，核对后补充到这里。
"""
NAME_FIX = {
    # 示例: '林业Ⅱ': '林业II',
}
