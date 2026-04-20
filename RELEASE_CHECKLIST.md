# 发布检查清单

## 当前建议的发布定位

- 保持 `public beta`
- 正式支持入口只写 `livingroom` 和 `coursedetail`
- 不把 `SPoC notice` 当正式能力宣传
- 安装方式以 `npx skills add <owner>/<repo> --skill <skill-name>` 为主

## 发布前你最好再亲自确认一次

- 在一台干净环境上按文档完成一次安装
- 至少验证一次非 Windows 下的 runtime auth
- 按公开文档完整跑一次端到端流程
- 确认仓库里没有个人 token、签名链接、真实用户名、真实路径
- 确认对外口径接受“agent 负责语义重建，脚本不直接承诺最终高质量纪要”

## 已经完成的部分

- 仓库根文档已经统一到 transcript-first 口径
- 正式支持入口已经收口到 `livingroom` / `coursedetail`
- `ppt` 已经降到辅助地位
- seed note 不再作为默认用户可见中间产物
- skill 文档已经改成 `python scripts/...` 相对路径写法
- 两个 skill 都有最小测试
- transcript coverage 降级策略已经写进文档
- 仓库已加入 `MIT` 许可证
- 根文档已经切到命令行安装优先的口径

## 还可以后续再做，但不阻塞这次发布

- GitHub 仓库简介和 topics 微调
- 非 Windows 再补一次实机截图或示例
- 如果后面要长期维护，再考虑是否拆成两个独立仓库
- 等公开安装量起来后，再观察 `skills.sh` / `find-skills` 的收录情况
