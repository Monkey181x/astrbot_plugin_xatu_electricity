# astrbot_plugin_xatu_electricity

![Version](https://img.shields.io/badge/version-v0.4.0-2563eb)
![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.9.2%2C%3C5-7c3aed)
![Python](https://img.shields.io/badge/Python-3.10%2B-0f766e)

西安工业大学电费查询与余额预警插件。插件通过学校统一身份认证获取缴费平台 `X-Token`，支持宿舍余额查询、近 30 天折线图统计，以及定时低余额提醒。

> 本项目是非官方插件，仅供本人账号及已获授权的宿舍信息查询使用。学校认证流程或缴费接口发生变化时，插件可能需要同步更新。

## 功能

- 无浏览器完成统一身份认证 CAS 登录，`X-Token`，失效后自动重新认证并重试；
- 根据公寓号和宿舍号自动查找准确的 `roomid`；
- 查询当前剩余电费；
- 保存最近 30 天查询记录并生成折线图；
- 每天定时查询预警列表，寒暑假自动暂停；
- 余额跨过 `30 / 15 / 5 / 0` 元档位时发送提醒；

## 兼容性

| 项目 | 要求 |
| --- | --- |
| AstrBot | `>=4.9.2,<5` |
| Python | 3.10 或更高版本 |

## 安装

### AstrBot WebUI

在 AstrBot WebUI 的插件管理页面选择从 GitHub 仓库安装，填入：

```text
https://github.com/Monkey181x/astrbot_plugin_xatu_electricity
```

安装完成后重载插件。

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Monkey181x/astrbot_plugin_xatu_electricity.git
```

AstrBot 会根据 `requirements.txt` 安装依赖。如果依赖未自动安装，可在 AstrBot 环境中执行：

```bash
pip install -r data/plugins/astrbot_plugin_xatu_electricity/requirements.txt
```

## 快速开始

1. 在插件配置中填写统一身份认证账号和密码，或者向机器人发送 `设置账密`。
2. 查询一次宿舍余额：

```text
查询电费 3-128
```

4. 需要定时提醒时发送：

```text
设置预警 3-128
```

公寓号与宿舍号使用 `-` 分隔。插件会处理缴费平台内部楼号映射，例如 `10-128` 表示 10 号公寓 128 宿舍。

## 指令

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `设置账密` | 管理员 | 依次接收下一条账号和密码，验证成功后保存凭据与 Token |
| `查询电费 公寓号-宿舍号` | 用户 | 查询余额，并按一小时去重规则写入历史 |
| `电费统计 公寓号-宿舍号` | 用户 | 根据本地近 30 天记录生成折线图，不请求学校接口 |
| `设置预警 公寓号-宿舍号` | 用户 | 为当前用户添加指定宿舍预警 |
| `取消预警 公寓号-宿舍号` | 用户 | 取消当前用户的指定宿舍预警 |

示例：

```text
查询电费 3-128
电费统计 3-128
设置预警 3-128
取消预警 3-128
```

## 插件配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `username` | 空 | 学校统一身份认证账号 |
| `password` | 空 | 学校统一身份认证密码 |
| `x_token` | 空 | 当前缴费平台 Token，由插件自动保存和更新 |
| `initial_token_env` | `XATU_X_TOKEN` | 首次启动时导入 Token 的环境变量名 |
| `project_id` | 内置项目 ID | 缴费平台电费项目 ID |
| `area_id` | `1` | 校区区域 ID |
| `request_timeout_seconds` | `20.0` | 网络请求超时时间 |
| `verify_tls` | `true` | 是否验证 HTTPS 证书 |
| `prewarm_auth` | `false` | 启动时没有 Token 是否立即认证 |
| `alert_check_hour` | `13` | 每日预警查询小时，北京时间，取值 0-23 |
| `winter_vacation_start` | `02-01` | 寒假开始日期，包含当天 |
| `winter_vacation_end` | `03-01` | 寒假结束日期，包含当天 |
| `summer_vacation_start` | `07-10` | 暑假开始日期，包含当天 |
| `summer_vacation_end` | `08-31` | 暑假结束日期，包含当天 |

也可以在首次启动前提供已有 Token：

```bash
XATU_X_TOKEN=当前有效的Token
```

## 预警规则

1. 默认每天北京时间 `13:00` 执行一次。
2. 寒暑假日期内完全跳过自动查询，手动查询不受影响。
3. 同一宿舍即使有多个用户订阅，每轮也只请求一次学校接口。
4. 自动查询结果同样写入近 30 天历史，并遵守一小时去重规则。
5. 余额从上一次自动检查值向下跨过 `30`、`15`、`5` 或 `0` 元时提醒。
6. 余额保持在同一档位不会重复提醒；充值回升后再次跌破档位会重新提醒。
7. 首次自动检查已经低于 30 元时，会提醒当前所在的最低档位。
8. 一次跨过多个档位时只发送最低已达到档位，避免连续发送多条消息。
9. 手动“查询电费”会记录历史，但不会直接触发或更新预警档位。

提醒优先发送私聊。若私聊失败，并且用户是在群聊中设置的预警，插件会回到该群聊 `@用户`，并提示添加机器人为好友。在私聊中设置预警时没有可用的群聊回退目标。

## Token 与自动认证

查询遵循以下顺序：

1. 读取并直接使用已保存的 `X-Token`，不会仅根据本地 JWT 时间提前登录。
2. 接口返回新 Token 时，保存后自动重试原请求。
3. 接口明确返回鉴权失败或 HTTP `401/403` 时，清除旧 Token。
4. 使用插件配置中保存的账号密码重新完成 CAS 登录。
5. 获取新 Token 后自动重试原电费查询。


## 数据存储

| 数据 | 位置或方式 |
| --- | --- |
| 账号、密码、Token、插件配置 | `data/config/astrbot_plugin_xatu_electricity_config.json` |
| 查询历史 | AstrBot 插件 KV，键名 `balance_history_v1` |
| 预警订阅与上次余额 | AstrBot 插件 KV，键名 `electricity_alerts_v1` |
| 统计图 | `data/plugin_data/astrbot_plugin_xatu_electricity/charts/` |

历史记录只保留最近 30 天。

## 开发与测试

```bash
python -m pip install -r requirements.txt
python -m unittest discover -v
```

可选代码检查：

```bash
ruff check main.py xatu_electricity tests
ruff format --check main.py xatu_electricity tests
```

项目结构：

```text
.
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt
├── xatu_electricity/
│   ├── alerts.py
│   ├── client.py
│   ├── history.py
│   └── ...
└── tests/
```


## 相关文档

- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot 主动消息](https://docs.astrbot.app/dev/star/guides/send-message.html)
- [AstrBot 插件存储](https://docs.astrbot.app/dev/star/guides/storage.html)

