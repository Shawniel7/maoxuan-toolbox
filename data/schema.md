# `entries.json` 数据结构

一个条目 = 一个"思想工具"。字段如下(**粗体** = 必填):

```jsonc
{
  // 短 slug,用于 URL 锚、localStorage key
  "id": "main-contradiction",

  // 中文标题,出现在卡片上方
  "title": "抓主要矛盾",

  // 出处(书名 + 年份),出现在标题下方
  "theory_source": "《矛盾论》(1937)",

  // 一句话总结,不超过 40 字
  "one_liner": "事物有很多矛盾……",

  // 主题标签,用于 browse 页展示 + 匹配算法计分(+2 / 命中)
  "tags": ["焦虑", "选择困难"],

  // 触发关键词:用户输入里出现任何一个,该条目得 +3 分
  // 关键词尽量短(2-6 字)、贴近用户的口头表达
  "trigger_keywords": ["不知道", "太多事", "迷茫"],

  // 原文节选。数组,可以多条。
  // 每条 text <= 80 字,source 明确到节次,url 指向公开权威全文
  "original_excerpts": [
    {
      "text": "研究任何过程……",
      "source": "《矛盾论》第四节",
      "url": "https://www.marxists.org/chinese/maozedong/..."
    }
  ],

  // 现代译读:把经典应用到当代具体场景。150 - 300 字为宜
  "modern_interpretation": "你焦虑的清单……",

  // 类比故事:一个让理论落地的历史/当代故事
  "analogy_story": {
    "title": "长征中的北上与南下",
    "body": "1935 年……"
  },

  // 行动清单:3 - 5 条可直接执行的动作
  "action_checklist": [
    "把当前所有焦虑点列出来",
    "……"
  ],

  // 延伸阅读,2 - 4 条。URL 同样要可访问
  "further_reading": [
    { "title": "《矛盾论》全文", "url": "https://..." }
  ],

  // 注意事项:这个工具在什么场景下会被误用
  "caveats": "主要矛盾会变化,别把一次判断当终身答案。"
}
```

## 字段写作指南

- **`id`**: 小写英文,以 `-` 连接,全局唯一。
- **`one_liner`**: 写给一个没读过原著的普通人听,一口气能念完。
- **`trigger_keywords`**: 设想三个不同人描述同一困惑的原话,从里面抽取共同的短语。不要放太虚的词(如"人生"、"成长"),要放具体的表述(如"不知道先做什么")。
- **`modern_interpretation`**: 不要堆术语。如果一句话出现三个抽象名词,改。
- **`analogy_story`**: 历史案例优先,当代类比次之。故事要有张力 —— 有抉择、有代价、有后果。
- **`action_checklist`**: 每一条是一个动词开头的动作。"多反思"不是动作,"每季度固定一天什么都不做只复盘"才是。
- **`caveats`**: 诚实写出这个工具被过度使用或误用的方式。

## 匹配算法打分

```
trigger_keywords 命中  +3 分 / 个
tags 命中              +2 分 / 个
title 命中             +1 分
```

最多返回前三条(得分 > 0)。完全无命中时,系统自动推荐三个基础工具:
`main-contradiction`、`seek-truth-from-facts`、`practice-theory-cycle`。
