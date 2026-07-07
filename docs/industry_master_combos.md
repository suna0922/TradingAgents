# A股分行业投资大师组合方案

> 为 TradingAgents 的每个辩论角色，按 A股行业特征匹配最适配的投资大师方法论。
> 每个行业配置 Bull / Bear / Aggressive / Conservative / Neutral / Portfolio Manager 全套6角色。
> 可通过 `set_config({"master_config": industry_preset("tech_innovation")})` 一键加载。

---

## 行业总览

| # | 行业类别 | 申万对应 | 核心驱动 | 估值特点 | 推荐主风格 |
|---|---------|---------|---------|---------|-----------|
| 1 | 科技创新 | 电子/计算机/通信 | 技术迭代、国产替代、政策 | 高PE/PS，成长溢价 | 成长+逆向 |
| 2 | 新能源 | 电力设备 | 政策驱动、产能周期、出海 | 周期+成长双属性 | 成长+周期 |
| 3 | 消费白马 | 食品饮料/家电/商贸/美容 | 品牌壁垒、渠道、消费力 | PE/DCF，确定性溢价 | 价值投资 |
| 4 | 医药医疗 | 医药生物 | 研发管线、集采政策、出海 | 管线DCF+PE，高风险高回报 | 成长+黑天鹅防御 |
| 5 | 金融 | 银行/非银金融 | 利率周期、信用周期、政策 | 低PB/PE，资产质量为王 | 价值+防御 |
| 6 | 周期资源 | 有色/煤炭/化工/钢铁/石化 | 大宗商品价格、供需周期、库存 | 周期PE陷阱（低PE买高点） | 宏观+周期 |
| 7 | 高端制造 | 机械/军工/汽车 | 产业升级、国产替代、订单 | PE+PEG，订单可见度 | 成长+宏观 |
| 8 | 地产基建 | 房地产/建筑/建材 | 政策、信用、销售数据 | 低PB/NAV，政策博弈 | 价值+逆向 |
| 9 | 公用环保 | 公用事业/环保/交运 | 利率、稳定现金流、特许经营 | DDM/DCF，股息率 | 防御+配置 |
| 10 | 农业养殖 | 农林牧渔 | 猪周期/禽周期、粮食价格 | 周期PE反向（高PE买低点） | 周期+逆向 |

---

## 1. 科技创新（半导体/AI/软件/通信/消费电子）

### 行业特征
- **高成长高波动**：技术迭代快，3-5年一代技术周期
- **国产替代主线**：美国制裁倒逼自主可控，设备/材料/EDA等环节加速
- **政策催化密集**：大基金、科技专项、信创订单
- **估值两极分化**：龙头享受成长溢价（PE 50-100x），尾部公司估值陷阱
- **A股特有**：题材炒作严重，概念股与真成长股鱼龙混杂

### 大师组合

| 角色 | 大师 | 理由 |
|------|------|------|
| **Bull Researcher** | `fisher` 费雪 | "闲聊法"调研技术壁垒、客户验证、研发团队——科技股最需要定性调研，费雪15要点完美适配 |
| **Bear Researcher** | `marks` 马克斯 | 识别科技泡沫的钟摆顶部——科技股最容易在情绪高点透支估值，马克斯二级思维是最佳空头视角 |
| **Aggressive Debator** | `wood` 伍德(ARK) | 颠覆性创新不惧高估值——AI/半导体颠覆传统产业的逻辑，伍德5大创新平台框架直接可用 |
| **Conservative Debator** | `klarman` 卡拉曼 | 极致安全边际——科技股下行风险巨大，卡拉曼的"不亏钱"原则+40%现金策略是最佳防御 |
| **Neutral Debator** | `aqr` AQR | 多因子量化平衡——价值+动量+质量因子交叉验证，避免单一视角偏差 |
| **Portfolio Manager** | `lynch` 林奇 | GARP（合理价格成长）——科技股不能纯看估值也不能不看，林奇的PEG和"快速增长型"分类最实用 |

### A股适配要点
- 国产替代率是核心指标：设备/材料/EDA等环节替代率<20%时是最佳介入点
- 区分"真科技"与"伪概念"：研发费用率>10%、专利数、核心客户验证
- 政策周期跟踪：大基金投向、科技专项清单、信创招标
- 题材股警惕：蹭概念但无实质研发投入的公司，马克斯/卡拉曼视角可过滤

```yaml
# 一键加载
tech_innovation:
  bull_researcher: fisher
  bear_researcher: marks
  aggressive_debator: wood
  conservative_debator: klarman
  neutral_debator: aqr
  portfolio_manager: lynch
  fundamentals_analyst: fisher
  market_analyst: raschke
```

---

## 2. 新能源（光伏/锂电/风电/储能/新能源车）

### 行业特征
- **双重属性**：成长性（渗透率提升）+ 周期性（产能过剩/出清）
- **政策驱动强**：补贴退坡、双碳目标、电改、出口退税
- **产业链长**：上游资源（锂/硅）→ 中游制造（电池/组件）→ 下游应用（电站/整车）
- **产能周期是核心**：扩产周期1.5-2年，供需错配导致剧烈周期波动
- **A股特有**：龙头集中度提升，二三线产能出清是投资主线

### 大师组合

| 角色 | 大师 | 理由 |
|------|------|------|
| **Bull Researcher** | `zhang_lei` 张磊 | "长坡厚雪"——新能源渗透率仍有巨大空间，张磊的产业链布局思维适合找长期赢家 |
| **Bear Researcher** | `bury` 伯里 | 极深逆向——产能周期顶点时全行业扩产，伯里的极致基本面分析能识别产能过剩拐点 |
| **Aggressive Debator** | `soros` 索罗斯 | 反身性大赌注——新能源政策催化+产能出清的拐点时刻，索罗斯反身性理论捕捉正反馈循环 |
| **Conservative Debator** | `marks` 马克斯 | 周期钟摆防御——新能源周期属性强，马克斯的"周期是什么位置"是最佳防御框架 |
| **Neutral Debator** | `munger` 芒格 | 逆向思维平衡——"反过来想"：如果产能不过剩？如果技术路线变了？芒格思维避免单向极端 |
| **Portfolio Manager** | `ptj` 保罗·都铎·琼斯 | 宏观+技术节奏——新能源受全球能源政策/大宗商品/汇率多重影响，PTJ的宏观+技术结合最全面 |

### A股适配要点
- 产能利用率是核心指标：>85%为景气，<70%为过剩
- 关注库存周期：硅料/锂盐库存天数→价格拐点先行指标
- 技术路线风险：PERC→TOPCon→HBC，磷酸铁锂→固态电池，路线切换颠覆格局
- 出海逻辑：海外认证、本地化产能、贸易壁垒（反倾销/关税）

```yaml
new_energy:
  bull_researcher: zhang_lei
  bear_researcher: bury
  aggressive_debator: soros
  conservative_debator: marks
  neutral_debator: munger
  portfolio_manager: ptj
  fundamentals_analyst: lynch
  market_analyst: livermore
```

---

## 3. 消费白马（白酒/食品饮料/家电/零售/美妆）

### 行业特征
- **品牌护城河**：品牌力是核心竞争力，强者恒强
- **现金流稳定**：预收款/应收款模式，经营现金流优于利润
- **渠道为王**：经销商体系、终端覆盖率、库存周期
- **消费力敏感**：居民收入/消费信心/人口结构影响长期增长
- **A股特有**：白酒是A股独有的消费垄断品类，茅台五粮液具备极强定价权

### 大师组合

| 角色 | 大师 | 理由 |
|------|------|------|
| **Bull Researcher** | `buffett` 巴菲特 | 护城河+消费垄断——巴菲特方法论为消费品量身定做，品牌壁垒/定价权/复利增长完美适配 |
| **Bear Researcher** | `graham` 格雷厄姆 | 安全边际——消费股在高估值时（如白酒PE>50x）需要格雷厄姆的绝对估值锚来警示风险 |
| **Aggressive Debator** | `lynch` 林奇 | GARP+十倍股——消费股是林奇"快速增长型"和"稳健增长型"的主战场，PEG<1时是最佳买点 |
| **Conservative Debator** | `duan_yongping` 段永平 | 本分+能力圈——段永平对消费品（OPPO/步步高）的深刻理解，"本分"原则防止追高 |
| **Neutral Debator** | `munger` 芒格 | 逆向思维——消费股"所有人都看好时"是否该谨慎？芒格的多学科思维平衡多空 |
| **Portfolio Manager** | `buffett` 巴菲特 | 长期持有+集中配置——消费白马适合巴菲特式集中持有，DCF估值+护城河宽度决定仓位 |

### A股适配要点
- 白酒核心指标：批价（茅台批价是消费板块风向标）、库存天数、预收款
- 品牌力评估：提价能力（能否跑赢通胀）、复购率、渠道利润率
- 消费降级/升级周期：关注客单价变化、下沉市场渗透
- 食品安全黑天鹅：塑化剂/三聚氰胺类事件是逆向买入机会（芒格/段永平视角）

```yaml
consumer:
  bull_researcher: buffett
  bear_researcher: graham
  aggressive_debator: lynch
  conservative_debator: duan_yongping
  neutral_debator: munger
  portfolio_manager: buffett
  fundamentals_analyst: buffett
  market_analyst: lynch
```

---

## 4. 医药医疗（创新药/器械/CXO/中药/医疗服务）

### 行业特征
- **研发驱动**：创新药管线价值>当期利润，临床数据是核心催化剂
- **政策敏感**：集采、医保谈判、审评审批政策直接影响估值
- **黑天鹅频发**：临床失败、FDA拒批、集采丢标——单事件可腰斩
- **出海打开天花板**：BD授权、海外临床、FDA获批是估值跃升催化剂
- **A股特有**：中药独家品种、CXO工程师红利、器械国产替代

### 大师组合

| 角色 | 大师 | 理由 |
|------|------|------|
| **Bull Researcher** | `fisher` 费雪 | 成长定性+闲聊法——创新药需要调研研发团队/临床进度/竞争格局，费雪15要点适配医药研发 |
| **Bear Researcher** | `taleb` 塔勒布 | 黑天鹅防御——医药临床失败/集采是典型黑天鹅，塔勒布的尾部风险思维+杠铃策略是最佳空头 |
| **Aggressive Debator** | `wood` 伍德(ARK) | 颠覆性医疗创新——基因编辑/ADC/GLP-1/AI制药，伍德的创新平台框架直接适配 |
| **Conservative Debator** | `klarman` 卡拉曼 | 极致安全边际——卡拉曼本身管理Baupost基金以深度价值+事件驱动著称，医药逆向投资经验丰富 |
| **Neutral Debator** | `aqr` AQR | 多因子平衡——医药股质量因子（研发投入/管线深度）+价值因子（PE/Pipeline NAV）交叉验证 |
| **Portfolio Manager** | `qiu_guolu` 邱国鹭 | 医药行业投资框架——邱国鹭《投资中最简单的事》中对医药/品牌的深度分析，A股本土视角最强 |

### A股适配要点
- 创新药估值：rNPV（风险调整净现值）+ 峰值销售额×概率
- 集采影响测算：中标价×量增 vs 老价格×量缩的净效应
- CXO跟踪指标：在手订单、产能利用率、海外收入占比
- 中药逻辑：独家品种+OTC渠道+品牌溢价，消费属性>医药属性
- 黑天鹅清单：临床III期数据、FDA/EMA审评、集采目录

```yaml
pharma:
  bull_researcher: fisher
  bear_researcher: taleb
  aggressive_debator: wood
  conservative_debator: klarman
  neutral_debator: aqr
  portfolio_manager: qiu_guolu
  fundamentals_analyst: fisher
  market_analyst: marks
```

---

## 5. 金融（银行/保险/券商）

### 行业特征
- **低估值高杠杆**：PB常年在0.5-1.0x，ROE 10-15%，杠杆10-15x
- **资产质量为王**：不良率、拨备覆盖率、关注类贷款迁徙率
- **利率周期敏感**：银行NIM（净息差）、保险投资端、券商自营
- **政策强监管**：MPA考核、偿二代、风控指标
- **A股特有**：国有大行"分红率+低PB"类债券属性，券商是牛市旗手

### 大师组合

| 角色 | 大师 | 理由 |
|------|------|------|
| **Bull Researcher** | `buffett` 巴菲特 | 银行护城河——巴菲特重仓银行（富国/美国银行），低成本存款护城河+ROE分析框架完美适配 |
| **Bear Researcher** | `bury` 伯里 | 信用风险逆向——伯里在2008年通过深度分析房贷违约率做空金融股，信用周期顶点识别能力最强 |
| **Aggressive Debator** | `soros` 索罗斯 | 反身性+金融杠杆周期——金融股的顺周期杠杆放大效应，索罗斯反身性理论捕捉金融周期拐点 |
| **Conservative Debator** | `graham` 格雷厄姆 | 防御+低PB——格雷厄姆的净流动资产/低PB策略对银行股天然适配，"烟蒂"思路选低估值金融 |
| **Neutral Debator** | `swensen` 斯文森 | 机构配置思维——耶鲁模式的核心是资产配置，金融股作为利率敏感资产在组合中的配置比例 |
| **Portfolio Manager** | `dalio` 达利欧 | 风险平价+宏观——金融股是宏观周期(利率/信用/经济)的β，达利欧的全天候+经济机器框架最全面 |

### A股适配要点
- 银行核心指标：净息差(NIM)、不良率、拨备覆盖率、ROE
- 券商逻辑：牛市β+自营弹性+财富管理转型
- 保险三差：死差（保障型）、费差（成本控制）、利差（投资端）
- 政策跟踪：LPR变动、降准降息、金融监管政策
- 隐蔽风险：表外资产、地方债敞口、房地产贷款集中度

```yaml
finance:
  bull_researcher: buffett
  bear_researcher: bury
  aggressive_debator: soros
  conservative_debator: graham
  neutral_debator: swensen
  portfolio_manager: dalio
  fundamentals_analyst: graham
  market_analyst: dalio
```

---

## 6. 周期资源（有色/煤炭/化工/钢铁/石油石化）

### 行业特征
- **大宗商品定价**：全球供需+美元+地缘政治决定价格
- **周期PE陷阱**：高PE（盈利底部）是买点，低PE（盈利顶部）是卖点——与直觉相反
- **库存周期**：主动补库→被动补库→主动去库→被动去库，4阶段
- **资本开支周期**：矿山/油田开发周期3-7年，供给刚性
- **A股特有**：煤炭高股息（分红率50%+）、有色跟随LME/SHFE价差

### 大师组合

| 角色 | 大师 | 理由 |
|------|------|------|
| **Bull Researcher** | `ptj` 保罗·都铎·琼斯 | 宏观+技术节奏——周期股核心是大宗价格趋势，PTJ的宏观分析+技术择时是最全面的周期多头 |
| **Bear Researcher** | `marks` 马克斯 | 周期钟摆顶部——马克斯的"周期是什么位置"是判断周期顶部的最佳框架，"好到不真实"时该做空 |
| **Aggressive Debator** | `druckenmiller` 德鲁肯米勒 | 不对称集中下注——周期拐点（如铜/油突破）时重仓，德鲁肯米勒的风险回报不对称框架 |
| **Conservative Debator** | `klarman` 卡拉曼 | 安全边际+商品底——卡拉曼对大宗商品的深度价值分析，在周期底部寻找资产负债表坚固的公司 |
| **Neutral Debator** | `aqr` AQR | 多因子+周期因子——AQR的商品因子/动量因子/价值因子交叉验证，避免单一周期判断偏差 |
| **Portfolio Manager** | `ptj` 保罗·都铎·琼斯 | 宏观+技术——周期资源股是宏观β的极致体现，PTJ的200日均线+宏观判断是最佳仓位管理 |

### A股适配要点
- 周期PE反向：PE高（盈利底）买，PE低（盈利顶）卖
- 库存指标：LME/SHFE库存、社会库存、港口库存
- 成本曲线：行业边际成本线是价格底，龙头成本优势=安全边际
- 资本开支：Capex/DA拐点预示2-3年后供给变化
- 高股息策略：煤炭/石油龙头分红率50%+，类债券属性

```yaml
cyclical:
  bull_researcher: ptj
  bear_researcher: marks
  aggressive_debator: druckenmiller
  conservative_debator: klarman
  neutral_debator: aqr
  portfolio_manager: ptj
  fundamentals_analyst: dalio
  market_analyst: livermore
```

---

## 7. 高端制造（机械/军工/汽车/通用设备）

### 行业特征
- **产业升级主线**：从"中国制造"到"中国智造"，国产替代+出口升级
- **订单驱动**：订单可见度（在手订单/年收入）是估值核心
- **技术壁垒分化**：高端装备（半导体设备/五轴机床）壁垒高，通用设备壁垒低
- **军民融合**：军工订单周期+技术溢出
- **A股特有**：军工信息不透明、汽车智能化转型、专精特新

### 大师组合

| 角色 | 大师 | 理由 |
|------|------|------|
| **Bull Researcher** | `fisher` 费雪 | 成长定性+技术调研——高端制造需要深度调研技术壁垒/客户验证/研发团队，费雪闲聊法适配 |
| **Bear Researcher** | `graham` 格雷厄姆 | 安全边际——制造业订单波动大，格雷厄姆的资产底+低PB策略在下行周期提供保护 |
| **Aggressive Debator** | `druckenmiller` 德鲁肯米勒 | 不对称集中——军工/汽车智能化拐点时集中下注，德鲁肯米勒的风险回报框架 |
| **Conservative Debator** | `schloss` 施洛斯 | 极简统计低估——施洛斯的低PB/低PE/高分红策略筛选被低估的制造企业 |
| **Neutral Debator** | `munger` 芒格 | 逆向思维——"反过来想"：如果国产替代不及预期？如果订单延后？芒格避免单向极端 |
| **Portfolio Manager** | `zhang_lei` 张磊 | 产业链布局——张磊的"长期主义+产业研究"框架，高端制造需要全产业链视角 |

### A股适配要点
- 军工逻辑：订单周期（3-5年）+型号定型+产能释放，信息不透明需多源验证
- 汽车智能化：智能驾驶芯片/激光雷达/域控制器，关注定点+量产节奏
- 国产替代率：半导体设备(<20%)、五轴机床(<10%)、高端轴承(<20%)
- 专精特新：工信部"小巨人"清单，细分领域隐形冠军

```yaml
manufacturing:
  bull_researcher: fisher
  bear_researcher: graham
  aggressive_debator: druckenmiller
  conservative_debator: schloss
  neutral_debator: munger
  portfolio_manager: zhang_lei
  fundamentals_analyst: lynch
  market_analyst: ptj
```

---

## 8. 地产基建（房地产/建筑/建材）

### 行业特征
- **政策博弈**：限购/限贷/限价/土地政策直接决定行业景气
- **高杠杆+信用风险**：房企三道红线、信用债到期、保交楼
- **销售数据先行**：30城成交面积/百强房企销售是估值核心驱动
- **NAV估值**：土地储备/在建/已售未结的NAV折价率
- **A股特有**：国企vs民企分化加剧，城投化趋势

### 大师组合

| 角色 | 大师 | 理由 |
|------|------|------|
| **Bull Researcher** | `graham` 格雷厄姆 | 安全边际+低PB——地产股当前普遍破净(PB<1)，格雷厄姆的资产底策略是逆向买入的核心逻辑 |
| **Bear Researcher** | `bury` 伯里 | 信用风险逆向——伯里2008年通过深度分析房贷违约做空地产，对高杠杆+信用周期的识别最强 |
| **Aggressive Debator** | `soros` 索罗斯 | 反身性+政策催化——地产政策转向时的反身性循环（政策放松→销售回暖→信用修复→估值修复） |
| **Conservative Debator** | `klarman` 卡拉曼 | 安全边际+不良资产——卡拉曼的深度价值+特殊机遇投资，地产不良资产/困境反转是他的领域 |
| **Neutral Debator** | `marks` 马克斯 | 周期判断——地产是典型周期行业，马克斯的"周期位置"判断+钟摆理论平衡多空 |
| **Portfolio Manager** | `marks` 马克斯 | 周期+信用——地产投资核心是周期位置+信用风险，马克斯的框架最全面 |

### A股适配要点
- 销售数据跟踪：30城日成交、百强房企月销售、二手房成交
- 信用风险：美元债收益率、境内债展期、三道红线达标情况
- 国企vs民企：国企融资成本低2-3pct，市场份额持续提升
- 建材逻辑：地产链后周期（水泥/防水/涂料），跟随竣工面积
- 保交楼影响：已售未结项目交付进度决定结算节奏

```yaml
real_estate:
  bull_researcher: graham
  bear_researcher: bury
  aggressive_debator: soros
  conservative_debator: klarman
  neutral_debator: marks
  portfolio_manager: marks
  fundamentals_analyst: graham
  market_analyst: marks
```

---

## 9. 公用环保（电力/水务/环保/交运）

### 行业特征
- **稳定现金流**：特许经营+政府定价，收入可预测性强
- **股息率策略**：分红率40-70%，股息率3-6%，类债券属性
- **利率敏感**：折现率变动影响DCF估值，降息周期受益
- **环保政策驱动**：碳交易、污水处理、固废处理政策
- **A股特有**：电力市场化改革、高速公路REITs、核电审批

### 大师组合

| 角色 | 大师 | 理由 |
|------|------|------|
| **Bull Researcher** | `buffett` 巴菲特 | 稳定现金流+护城河——巴菲特重仓公用事业（BPL/BHE），特许经营+稳定现金流是他的选股标准 |
| **Bear Researcher** | `graham` 格雷厄姆 | 安全边际——公用事业低增长，格雷厄姆的防御性投资原则（低PE+高股息+稳定盈利）适配 |
| **Aggressive Debator** | `lynch` 林奇 | GARP+稳定增长——公用事业中的优质龙头（如核电/水电）具备稳定成长性，林奇GARP框架 |
| **Conservative Debator** | `schloss` 施洛斯 | 极简低估值——施洛斯的低PB/低PE/高分红/长持有策略，与公用事业股完美适配 |
| **Neutral Debator** | `bogle` 博格尔 | 被动+低成本——公用事业最适合被动持有，博格尔的"不折腾"哲学平衡辩论 |
| **Portfolio Manager** | `swensen` 斯文森 | 机构配置——耶鲁模式的资产配置思维，公用事业作为"通胀保护+稳定收益"配置工具 |

### A股适配要点
- 股息率是核心：>4%具备配置价值，>5%具备深度价值
- 现金流质量：经营现金流/净利润>1.2，自由现金流转正
- 利率周期：10年期国债收益率下行→公用事业估值上行
- 电力市场化：电价浮动空间扩大，优质水电/核电受益
- REITs逻辑：高速公路/污水处理/产业园区REITs提供新退出渠道

```yaml
utility:
  bull_researcher: buffett
  bear_researcher: graham
  aggressive_debator: lynch
  conservative_debator: schloss
  neutral_debator: bogle
  portfolio_manager: swensen
  fundamentals_analyst: buffett
  market_analyst: dalio
```

---

## 10. 农业养殖（养殖/种植/饲料）

### 行业特征
- **强周期性**：猪周期3-4年一轮（能繁母猪→生猪供应滞后10个月）
- **周期PE反向**：亏损期（高PE/负PE）是买点，暴利期（低PE）是卖点
- **粮食安全**：大豆/玉米进口依赖度，种植链政策敏感
- **疫病风险**：非洲猪瘟/禽流感是典型黑天鹅
- **A股特有**：猪周期是A股最典型的周期博弈，牧原/温氏/新希望

### 大师组合

| 角色 | 大师 | 理由 |
|------|------|------|
| **Bull Researcher** | `ptj` 保罗·都铎·琼斯 | 宏观+周期节奏——猪周期本质是供需+价格周期，PTJ的宏观周期+技术择时框架最适配 |
| **Bear Researcher** | `bury` 伯里 | 极深逆向——猪周期顶点特征明确（全行业暴利+母猪补栏），伯里的极致基本面分析识别拐点 |
| **Aggressive Debator** | `soros` 索罗斯 | 反身性+周期——猪价上涨→补栏→过剩→下跌的反身性循环，索罗斯理论完美解释周期 |
| **Conservative Debator** | `marks` 马克斯 | 周期钟摆——养殖业的钟摆效应极强（亏损↔暴利），马克斯的周期位置判断是最佳防御 |
| **Neutral Debator** | `aqr` AQR | 多因子+周期因子——AQR的商品周期因子+动量因子+价值因子交叉验证猪周期 |
| **Portfolio Manager** | `ptj` 保罗·都铎·琼斯 | 周期节奏——养殖股核心是周期择时，PTJ的200日均线+宏观判断管理仓位 |

### A股适配要点
- 猪周期核心指标：能繁母猪存栏（农业农村部月度数据）、猪粮比、自繁自养头均利润
- 周期PE反向：头均亏损300+元（周期底）是买入信号，头均盈利1000+元（周期顶）是卖出信号
- 成本优势：牧原完全成本14-15元/kg，行业平均17-18元/kg，成本差=安全边际
- 疫病跟踪：非洲猪瘟发生率、疫苗进展
- 种植链：大豆/玉米价格、转基因商业化进度

```yaml
agriculture:
  bull_researcher: ptj
  bear_researcher: bury
  aggressive_debator: soros
  conservative_debator: marks
  neutral_debator: aqr
  portfolio_manager: ptj
  fundamentals_analyst: dalio
  market_analyst: livermore
```

---

## 快速使用指南

### 方式一：代码加载行业预设

```python
from tradingagents.masters.industry_presets import industry_preset, list_industries

# 查看所有行业
print(list_industries())
# ['tech_innovation', 'new_energy', 'consumer', 'pharma', 'finance',
#  'cyclical', 'manufacturing', 'real_estate', 'utility', 'agriculture']

# 获取行业预设
preset = industry_preset("tech_innovation")
# → {'bull_researcher': 'fisher', 'bear_researcher': 'marks', ...}

# 应用到系统
from tradingagents.dataflows.config import set_config
set_config({"master_config": preset})
```

### 方式二：环境变量

```bash
# 逐角色指定（科技股组合示例）
export TRADINGAGENTS_MASTER_BULL=fisher
export TRADINGAGENTS_MASTER_BEAR=marks
export TRADINGAGENTS_MASTER_AGGRESSIVE=wood
export TRADINGAGENTS_MASTER_CONSERVATIVE=klarman
export TRADINGAGENTS_MASTER_NEUTRAL=aqr
export TRADINGAGENTS_MASTER_PM=lynch
```

### 方式三：混合搭配

```python
from tradingagents.dataflows.config import set_config

# 消费白马组合，但把 Bull 换成林奇（更激进）
set_config({"master_config": {
    "bull_researcher": "lynch",        # 林奇 GARP
    "bear_researcher": "graham",       # 格雷厄姆安全边际
    "aggressive_debator": "fisher",    # 费雪成长定性
    "conservative_debator": "duan_yongping",  # 段永平本分
    "neutral_debator": "munger",       # 芒格逆向
    "portfolio_manager": "buffett",    # 巴菲特长期持有
}})
```

---

## 行业 × 大师交叉对照速查表

| 大师 | 科技 | 新能源 | 消费 | 医药 | 金融 | 周期 | 制造 | 地产 | 公用 | 农业 |
|------|:----:|:------:|:----:|:----:|:----:|:----:|:----:|:----:|:----:|:----:|
| 巴菲特 | | | **Bull/PM** | | **Bull** | | | | **Bull** | |
| 格雷厄姆 | | | **Bear** | | **Cons** | | **Bear** | **Bull** | **Bear** | |
| 费雪 | **Bull** | | | **Bull** | | | **Bull** | | | |
| 林奇 | **PM** | | **Aggr** | | | | | | **Aggr** | |
| 芒格 | | | **Neutral** | | | | **Neutral** | | | |
| 马克斯 | **Bear** | **Cons** | | | | **Bear/PM** | | **Neutral/PM** | | **Cons** |
| 索罗斯 | | **Aggr** | | | **Aggr** | | | **Aggr** | | **Aggr** |
| 达利欧 | | | | | **PM** | | | | | |
| 德鲁肯米勒 | | | | | | **Aggr** | **Aggr** | | | |
| PTJ | | **PM** | | | | **Bull/PM** | | | | **Bull/PM** |
| 卡拉曼 | **Cons** | | | **Cons** | | **Cons** | | **Cons** | | |
| 塔勒布 | | | | **Bear** | | | | | | |
| 伯里 | | **Bear** | | | **Bear** | | | **Bear** | | **Bear** |
| 张磊 | | **Bull** | | | | | **PM** | | | |
| 段永平 | | | **Cons** | | | | | | | |
| 邱国鹭 | | | | **PM** | | | | | | |
| 伍德 | **Aggr** | | | **Aggr** | | | | | | |
| AQR | **Neutral** | | | **Neutral** | | **Neutral** | | | | **Neutral** |
| 施洛斯 | | | | | | | **Cons** | | **Cons** | |
| 斯文森 | | | | | **Neutral** | | | | **PM** | |
| 博格尔 | | | | | | | | | **Neutral** | |
| 利弗莫尔 | | | | | | | | | | |
| 拉斯奇 | | | | | | | | | | |

> **Bull**=Bull Researcher, **Bear**=Bear Researcher, **Aggr**=Aggressive, **Cons**=Conservative, **Neutral**=Neutral, **PM**=Portfolio Manager

---

## 设计逻辑总结

### 三大匹配原则

1. **行业属性 → 大师风格匹配**
   - 成长型行业（科技/医药）→ 费雪/林奇/伍德（成长定性）
   - 价值型行业（消费/金融/公用）→ 巴菲特/格雷厄姆（价值护城河）
   - 周期型行业（周期/农业/新能源）→ PTJ/索罗斯/马克斯（宏观周期）
   - 困境型行业（地产）→ 格雷厄姆/卡拉曼（安全边际+困境反转）

2. **角色定位 → 大师特长匹配**
   - Bull（看多）→ 选最擅长发现价值的成长/价值派
   - Bear（看空）→ 选最擅长识别风险的逆向/周期派
   - Aggressive（激进）→ 选敢于重仓的趋势/颠覆派
   - Conservative（保守）→ 选极致安全边际的防御派
   - Neutral（中立）→ 选多因子/逆向思维的平衡派
   - PM（决策）→ 选综合能力最强的宏观/配置派

3. **A股特殊性 → 本土大师补充**
   - 张磊（产业链思维）、段永平（消费品理解）、邱国鹭（A股投资框架）
   - 梁文锋（量化本土化）—— 已在 YAML 库中但行业组合中未做主选
   - 政策驱动行业（新能源/地产/医药）特别需要本土视角
