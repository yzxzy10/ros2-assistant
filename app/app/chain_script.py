"""生成容器内执行的验证脚本：build → 起节点 → AI 注入 → 拓扑序逐跳断言 → 收日志。"""
import os

from .chain_msg import _fake_value, _sh_quote, _slug


def _base_of(node):
    """节点所在源文件名（不含扩展名），用来猜包内真实的可执行名。

    例：`demo_nodes_cpp/src/logging/use_logger_service.cpp::TestNode`
    的节点类名是 TestNode，但包里的可执行叫 use_logger_service ——
    只按类名去找永远对不上。
    """
    src = str(node.get("id") or "").split("::")[0].strip()
    if not src:
        return ""
    stem, ext = os.path.splitext(os.path.basename(src))
    return stem if ext in (".cpp", ".cc", ".cxx", ".py") else ""


def build_script(distro, plan, cfg):
    cv = (cfg.get("ros") or {}).get("chain_verify") or {}
    vcfg = (cfg.get("ros") or {}).get("verify") or {}
    hop_wait = int(cv.get("hop_timeout_sec") or 12)
    rate = int(cv.get("inject_rate") or 5)
    boot = int(cv.get("boot_wait_sec") or 8)
    # 并行度和容器 CPU 额度分开配：CPU 可以给 3~4 核，但并行编译的进程数
    # 必须按内存算 —— 每个 cc1plus 峰值 1~2GB，4G 内存下 3 并行就会触发 OOM，
    # 报出来是 `c++: fatal error: Killed signal terminated program cc1plus`，
    # 跟代码写错长得一模一样。默认 2，内存给足后可由管理员调大。
    workers = str(vcfg.get("build_jobs") or "2")
    # 包内并行度（单个包内部同时编译几个 .cpp）。
    # --parallel-workers 只管「同时编几个包」，管不到包里面 —— colcon 调
    # cmake --build 时不传 -j，cmake 就按核数全开。3 核 × 每核一个 cc1plus
    # ≈ 6GB，正好把 6G 的容器顶死，于是 demo_nodes_cpp 这种 29 个 cpp 的包
    # 每次都报 `c++: fatal error: Killed signal terminated program cc1plus`。
    # 这个环境变量是 cmake（3.12+）自己读的，make/ninja 都会跟着降，不用改包。
    pkg_jobs = str(vcfg.get("pkg_jobs") or workers)

    L = []
    A = L.append
    A("#!/usr/bin/env bash")
    A("# ros2 学习助手自动生成：链路级真实验证")
    A("source /opt/ros/%s/setup.bash || { echo __ENV_FAIL__; exit 3; }" % distro)
    # 容器里脚本在 /ws，本机 local 模式下在工作区 —— 用脚本自身位置定位，
    # 两边都能跑（local 后端以前 cd /ws 直接退出 3，等于永远跑不了）
    A('WS="$(cd "$(dirname "$0")" && pwd)"')
    A('cd "$WS" || exit 3')
    # 只设新的发现范围：ROS_LOCALHOST_ONLY 已废弃，设了会让每个节点刷两条 WARN
    A("export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST")
    A("mkdir -p /tmp/nlogs")
    A('echo "__STAGE__|deps"')
    # 本地包池：能补就补，补不上也**不拦编译**。
    # 之前这里是「缺一个包就 exit 5」，结果是任何带自定义 msg 包的工作空间
    # （ros-jazzy-robot-msgs 这类本地包）都会在依赖阶段被掐死，colcon 一次都没跑，
    # 用户只看到「缺依赖」，根本看不到自己项目真正的编译结果。
    # 现在缺包只记一行标记，成败交给真实 colcon/节点去判 —— 真用到了它自然会报错，
    # 静态扫描猜错了也不会冤枉用户。
    A('if [ -n "$ROS2_PKGS" ]; then')
    A('  for p in $ROS2_PKGS; do')
    A('    dpkg -s "$p" >/dev/null 2>&1 && continue')
    A('    deb=$(ls /pkgpool/${p}_*.deb 2>/dev/null | head -n 1)')
    A('    if [ -z "$deb" ]; then echo "__PKG_MISSING__|$p"; continue; fi')
    A('    if dpkg -i "$deb" >/tmp/nlogs/dpkg.log 2>&1; then')
    A('      echo "__PKG_OK__|$p"')
    A('    elif dpkg -i --force-depends "$deb" >>/tmp/nlogs/dpkg.log 2>&1; then')
    A('      echo "__PKG_OK__|$p"')
    A('    else')
    A('      echo "__PKG_FAIL__|$p"')
    A('    fi')
    A('  done')
    A('fi')
    # 配置了 extra_apt（有网的机器）才走 apt，作为包池之外的兜底
    A('if [ -n "$ROS2_EXTRA_APT" ]; then')
    A('  echo "安装额外依赖: $ROS2_EXTRA_APT"')
    A('  apt-get update -qq >/dev/null 2>&1')
    A('  apt-get install -y -qq $ROS2_EXTRA_APT 2>&1 | tail -n 15')
    A('  [ "${PIPESTATUS[0]}" -eq 0 ] || echo "__APT_FAIL__"')
    A('fi')
    A('echo "__STAGE__|build"')
    # 见函数头注释：不压包内并行度，重 C++ 包必被 OOM killer 杀掉。
    #
    # 只设 CMAKE_BUILD_PARALLEL_LEVEL 不够 —— 线上实测 demo_nodes_cpp 仍然
    # 一次性炸掉六七个个 target，说明 make 还在按核数开 g++。所以三个一起上：
    #   1) 环境变量（cmake --build 读）
    #   2) MAKEFLAGS（GNU make 直接读，对不走 cmake --build 的包也有效）
    #   3) cmake cache 变量（同名 cache 变量优先级最高，前面两个会被它盖掉）
    # 顺带把 nproc / 生效值打进日志，下次再炸能一眼看出并行度到底是多少。
    A('export CMAKE_BUILD_PARALLEL_LEVEL=%s' % pkg_jobs)
    A('export MAKEFLAGS="-j%s"' % pkg_jobs)
    A('echo "[build] 并发：同时 %s 个包，包内 %s 个 g++；nproc=$(nproc) '
      'MAKEFLAGS=$MAKEFLAGS CPLV=$CMAKE_BUILD_PARALLEL_LEVEL"' % (workers, pkg_jobs))
    # 预检：ros:jazzy-ros-base 不保证自带 colcon（项目里的 Dockerfile.verify
    # 就是专门补这一步的）。没有它，脚本会以 127 退出、看起来像「编译不通过」，
    # 实际是环境缺件 —— 两者对用户是完全不同的处置动作，必须区分开。
    A('command -v colcon >/dev/null 2>&1 || { echo "__ENV_MISSING__|colcon"; exit 4; }')
    # 两个参数都是踩坑加的，别删：
    #
    # -DBUILD_TESTING=OFF —— 基础镜像里没有 ros_testing / ament_lint_auto 这些
    #   测试专用包，而大量官方包把 find_package(ros_testing REQUIRED) 写在
    #   if(BUILD_TESTING) 里（demos 的 lifecycle 就是）。默认 BUILD_TESTING=ON，
    #   于是一个纯测试依赖会让整个包编译失败，用户以为是自己的节点代码有问题。
    #   编译校验本来也不跑测试，关掉没有任何损失。
    #
    # --continue-on-error —— 没有它，colcon 遇到第一个失败包就停止调度，
    #   后面的包全是 "Aborted / not processed"。用户只能看到「1 个失败、21 个中止」，
    #   根本不知道剩下 30 个到底行不行，等于白跑一轮。编译校验的价值就是给出
    #   每个包的真实结论，所以必须让 colcon 把活干完。
    # 包总数先取出来，前端才能显示「已完成 12 / 45」，否则用户只知道在转圈
    A('NPKG=$(colcon list 2>/dev/null | wc -l | tr -d " ")')
    A('echo "__BUILD_TOTAL__|$NPKG"')
    #
    # 编译输出必须实时透传。这里曾经是：
    #     colcon build ... | tee build.log | tail -n 120
    # 后果很严重 —— tail 必须等整条输入流关闭才开始输出，colcon 的每一行
    # 进度都被堵在管道里。45 个包、17 个 C++ 包在 2 核容器里本来就要十几
    # 分钟，用户只看见「正在编译项目...」一动不动，无法区分「慢」和「卡死」，
    # 最后在 28 分钟硬超时被判失败，而真实原因可能只是没编完。
    #
    # 现在只放行 colcon 的阶段行（每包两行），全量仍然写进 build.log：
    # 既不刷屏，也能让前端算出「已完成 N / 总数」，进度条真的会动。
    A('colcon build --symlink-install --continue-on-error'
      ' --parallel-workers %s' % workers
      + ' --cmake-args -DBUILD_TESTING=OFF -DCMAKE_BUILD_PARALLEL_LEVEL=%s' % pkg_jobs
      + ' 2>&1'
      ' | tee /tmp/nlogs/build.log'
      ' | stdbuf -oL grep --line-buffered -E'
      ' "^(Starting|Finished|Failed|Aborted)[[:space:]]+(>>>|<<<)"')
    # PIPESTATUS 必须在紧邻管道的那一行立刻取，插任何命令都会变
    A('RC=${PIPESTATUS[0]}')
    A('if [ "$RC" -ne 0 ]; then')
    # 只回放尾部会把中间失败包的报错截掉（39 个包一起编，失败的那个往往在
    # 日志前半段，等 tail 拿到手只剩 summary）。所以把失败的包名单独捞出来，
    # 让用户至少知道该点开哪个包看。
    A('  echo "---- 编译失败的包 ----"')
    A('  grep -E "^(Failed|Aborted)[[:space:]]+<<<" /tmp/nlogs/build.log'
      ' | sed "s/[[:space:]]\\+/ /g" | head -n 40')
    A('  echo "---- 失败原因（每个包最后几行）----"')
    A('  grep -B 2 -A 8 "CMake Error\\|error:\\|ModuleNotFoundError"'
      ' /tmp/nlogs/build.log | head -n 80')
    #
    # ② 内存不足：自动降级补编。
    #
    # 为什么要有这一段：45 个包一起编时，真正被内存杀掉的往往只有一两个
    # 大包（demo_nodes_cpp 一个包 29 个 cpp，一个 cc1plus 峰值 1~2GB），
    # 其余三四十个包其实都编好了。以前这里直接 exit 2，等于让一个资源
    # 问题否掉整轮验证，用户还得从头再来一遍。
    #
    # 现在只针对「确实被 OOM killer 杀过」的轮次（靠 cc1plus / Killed
    # signal 判断），把失败的包单独拎出来、单线程重编：--parallel-workers 1
    # + CMAKE_BUILD_PARALLEL_LEVEL=1，任何时刻只有一个 g++，内存峰值最低。
    # 普通语法错误/缺头文件不重试 —— 重来一遍还是同样结果，纯浪费时间。
    # 跑两轮是因为失败包之间可能有依赖：第一轮 A 失败是因为 B 没编好，
    # B 补上之后第二轮 A 才能过。
    A('  OOMN=$(grep -c -E "Killed signal|cc1plus|internal compiler error'
      '|virtual memory exhausted" /tmp/nlogs/build.log || true)')
    A('  if [ "${OOMN:-0}" -gt 0 ]; then')
    A('    FAILED=$(awk "/^(Failed|Aborted) *<<</ {print \\$3}" /tmp/nlogs/build.log'
      ' | sort -u)')
    A('    echo "---- 内存不足，改为逐包单线程补编：$(echo $FAILED) ----"')
    A('    ORDER=$(colcon list --topological-order --names-only 2>/dev/null)')
    A('    [ -z "$ORDER" ] && ORDER=$(colcon list --names-only 2>/dev/null)')
    # 还空就别指望 colcon list 了，按失败顺序直接编。
    # 线上真踩过：ORDER 为空 → for 一次都没进 → 打「全部补编通过」，
    # install 里却什么都没有，后面所有节点起不来（72 跳全断）。
    A('    [ -z "$ORDER" ] && ORDER="$FAILED"')
    A('    echo "[retry] 待补编顺序：$ORDER"')
    A('    ROUND=0')
    A('    while [ -n "$FAILED" ] && [ "$ROUND" -lt 2 ]; do')
    A('      ROUND=$((ROUND+1)); LEFT=""')
    A('      for p in $ORDER; do')
    A('        case " $FAILED " in *" $p "*) ;; *) continue ;; esac')
    A('        echo "__RETRY__|$p|$ROUND"')
    A('        echo "[retry] $p：单线程重编（第 $ROUND 轮），最多 15 分钟"')
    A('        if CMAKE_BUILD_PARALLEL_LEVEL=1 MAKEFLAGS="-j1" timeout 900 colcon build'
      ' --symlink-install --packages-select "$p" --parallel-workers 1'
      ' --cmake-args -DBUILD_TESTING=OFF -DCMAKE_BUILD_PARALLEL_LEVEL=1'
      ' >> /tmp/nlogs/retry.log 2>&1; then')
    A('          RET_OK=1')
    A('        else RET_OK=0; fi')
    # 退出码 0 不等于真编出来了。colcon 对「build 目录已存在」的包有时
    # 秒过（什么都没做）就返回 0 —— 线上真踩到了：补编报通过，install 里
    # 却没有 share/<pkg>/local_setup.bash，接下来所有节点都起不来，72 跳全断，
    # 看起来像「节点代码错」，其实是补编根本没落地。所以拿产物说话。
    A('        if [ "$RET_OK" = "1" ] && [ -e "install/$p" ]; then')
    A('          echo "[retry] $p：重编通过（install/$p 已生成）"')
    A('          echo "__RETRY_OK__|$p"')
    A('        else')
    A('          echo "[retry] $p：重编失败"')
    A('          echo "---- retry.log 尾部 ----"')
    A('          tail -n 25 /tmp/nlogs/retry.log')
    A('          echo "__RETRY_FAIL__|$p"')
    A('          LEFT="$LEFT $p"')
    A('        fi')
    A('      done')
    A('      FAILED=$(echo $LEFT)')
    A('    done')
    A('    if [ -z "$FAILED" ]; then RC=0; echo "[retry] 全部补编通过，本轮编译算通过"; fi')
    A('  fi')
    A('  if [ "$RC" -ne 0 ]; then')
    A('    [ -s /tmp/nlogs/retry.log ] && { echo "---- 补编日志（最后 40 行）----";'
      ' tail -n 40 /tmp/nlogs/retry.log; }')
    A('    echo "__BUILD_FAIL__"; exit 2')
    A('  fi; fi')
    # 「这轮到底真编没真编」的实锤：数一下 build/ 下真落了多少个 .o 目标文件
    # （每个 .o 都是 g++ 实际跑过一次的产物），以及 install/ 下真落了几个库/可执行。
    # 不数日志行，是因为 colcon 默认把编译细节吞在 log/latest_build 里，
    # 成功时不往控制台打；.o 文件骗不了人，编译被跳过就不存在。
    # 环境退化成离线模拟时，这两个数字必然是 0 —— 一眼能分辨真假。
    A('CXXN=$(find build -name "*.o" 2>/dev/null | wc -l | tr -d " ")')
    A('ARTN=$(find install -type f \\( -name "*.so" -o -name "*.a" -o -perm -u+x \\) 2>/dev/null'
      ' | wc -l | tr -d " ")')
    A('echo "[build] 实编目标文件 ${CXXN:-0} 个（build 下 .o），install 产物 ${ARTN:-0} 个"')
    A('echo "__BUILD_REAL__|${CXXN:-0}|${ARTN:-0}"')
    A('echo "__BUILD_OK__"')
    A('. install/setup.bash || { echo __ENV_FAIL__; exit 3; }')

    # 话题名/设备号写在 .env 里的项目（os.environ['ROS_TOPIC_X']），
    # 不把配置表导进来，节点一起来就 KeyError 退出 —— 看起来像「节点崩溃」，
    # 实际只是少了一句 set -a。只取工作区里的几个，按路径排序保证可复现。
    A('for f in $(find . -maxdepth 4 -type f \\( -name "*.env" -o -name ".env" \\) '
      '-not -path "./build/*" -not -path "./install/*" -not -path "*/node_modules/*" '
      '2>/dev/null | sort | head -n 5); do')
    A('  echo "__ENV_FILE__|$f"')
    A('  set -a; . "./$f" >/dev/null 2>&1 || true; set +a')
    A('done')

    A('echo "__STAGE__|run"')
    # 容器自检：节点「一启动就崩」可能是可执行名不对、包没装进 install、
    # 或 ament index 没起来。这三件事的修法完全不同，先让容器把看到的报上来。
    A('echo "__DIAG__|pwd=$(pwd)"')
    A('echo "__DIAG__|install=$(ls install 2>&1 | paste -sd " " | head -c 240)"')
    A('echo "__DIAG__|pkgdir=$(ls install/composition/lib/composition 2>&1 | paste -sd " " | head -c 240)"')
    A('echo "__DIAG__|pkgexe=$(ros2 pkg executables composition 2>&1 | paste -sd " " | head -c 240)"')
    # 重点看几个「明明编译通过、可执行却找不到」的包：lib/<pkg> 里有什么、
    # lib 根下有什么。装歪了（落 lib/ 而不是 lib/<pkg>/）和根本没装，
    # 是两种完全不同的修法。
    for _p in ("demo_nodes_cpp", "examples_rclcpp_cbg_executor",
               "examples_rclcpp_minimal_publisher"):
        A('echo "__DIAG__|%s/lib/<pkg>=$(ls install/%s/lib/%s 2>&1 | paste -sd " " | head -c 260)"'
          % (_p, _p, _p))
        A('echo "__DIAG__|%s/lib=$(ls install/%s/lib 2>&1 | paste -sd " " | head -c 200)"'
          % (_p, _p))
    A("NPIDS=''")
    A("declare -A NPID2ID")
    A("declare -A ID2PID")
    # 节点日志必须无缓冲写盘：ROS 2 的 INFO 走 stdout，重定向到文件后
    # Python/C 都会块缓冲（8KB 才刷一次），而收尾是 SIGKILL —— 缓冲里的行
    # 直接蒸发，日志文件成了空文件，前端就只剩「日志收集中」。
    A('NB="env PYTHONUNBUFFERED=1"')
    A('command -v stdbuf >/dev/null 2>&1 && NB="$NB stdbuf -oL -eL"')
    # 可执行名纠错：对得上就用；对不上先按名字包含关系找；包内只有一个
    # 可执行就直接用它。三条都不成立才退回原名字，让它如实报错。
    A('_pick_exe() {')
    A('  local p="$1" want="$2" alt="$3" list m n')
    A('  list=$(ros2 pkg executables "$p" 2>/dev/null | awk \'{print $2}\')')
    A('  if [ -z "$list" ]; then')
    # ros2 pkg executables 走 ament index，线上对 --symlink-install 的包
    # 有时一个都不返回；install/<pkg>/lib/<pkg>/ 下面就是可执行本体，直接扫。
    A('    list=$(ls "install/$p/lib/$p" 2>/dev/null | grep -v "\\." | sort)')
    A('  fi')
    A('  if [ -z "$list" ]; then echo "$want"; return; fi')
    A('  if echo "$list" | grep -qx "$want"; then echo "$want"; return; fi')
    # 源码文件名往往比节点类名更接近可执行名：
    # TestNode 所在文件是 use_logger_service.cpp，可执行就叫 use_logger_service。
    A('  if [ -n "$alt" ] && echo "$list" | grep -qx "$alt"; then echo "$alt"; return; fi')
    # ping_node 这种：包里注册的可执行就叫 ping。只砍 _node 尾巴，别乱砍。
    A('  c="${want%_node}"')
    A('  if [ "$c" != "$want" ] && echo "$list" | grep -qx "$c"; then echo "$c"; return; fi')
    A('  if [ -n "$alt" ]; then c="${alt%_node}"; '
      'if [ "$c" != "$alt" ] && echo "$list" | grep -qx "$c"; then echo "$c"; return; fi; fi')
    # 可执行名常带包内前缀：examples_rclcpp_wait_set 里节点叫 random_talker，
    # 可执行叫 wait_set_talker —— 末段词一样，中间多了包名前缀。
    # 先按「末段词相同」找，命中取最短的那个（talker 优先于 talker_foo_bar）。
    # 比的是**末段词**：random_talker 的末段是 talker，可执行叫 wait_set_talker。
    A('  tw="${want##*_}"')
    A('  for c in $list; do case "$c" in *"_$tw") sfx="$c"; break;; esac; done')
    A('  if [ -n "$sfx" ]; then echo "$sfx"; return; fi')
    A('  if [ -n "$alt" ]; then')
    A('    ta="${alt##*_}"')
    A('    for c in $list; do case "$c" in *"_$ta") sfx="$c"; break;; esac; done')
    A('    if [ -n "$sfx" ]; then echo "$sfx"; return; fi')
    A('  fi')
    # 剩下的靠词元重合度：minimal_publisher_with_unique_network_flow_endpoints
    # 对应的是 publisher_member_function_with_unique_network_flow_endpoints，
    # 前缀是包名不是可执行名，光靠包含关系永远对不上。
    A('  best=""; bs=0')
    A('  for c in $list; do')
    A('    s=0')
    A('    for x in $(echo "$want" | tr "_" " "); do')
    A('      for y in $(echo "$c" | tr "_" " "); do [ "$x" = "$y" ] && s=$((s+1)); done')
    A('    done')
    A('    if [ -n "$alt" ]; then')
    A('      for x in $(echo "$alt" | tr "_" " "); do')
    A('        for y in $(echo "$c" | tr "_" " "); do [ "$x" = "$y" ] && s=$((s+1)); done')
    A('      done')
    A('    fi')
    A('    [ "$s" -gt "$bs" ] && { bs=$s; best="$c"; }')
    A('  done')
    A('  if [ -n "$best" ] && [ "$bs" -ge 2 ]; then echo "$best"; return; fi')
    A('  n=$(echo "$list" | wc -l | tr -d " ")')
    A('  if [ "$n" = "1" ]; then echo "$list"; return; fi')
    A('  echo "$want"')
    A('}')
    # 起节点之前先把「记录员」挂上：它按计划里的类型订阅所有待取证话题，
    # 只记「哪个话题来过消息」，不打印内容。这样启动瞬间发一次的消息
    # （use_logger_service 只发 4 条）也能抓到，不用按拓扑序一路等过去。
    A(': > /tmp/nlogs/rec.tsv')
    if plan["hops"]:
        A('python3 $WS/hop_recorder.py /tmp/nlogs/rec.tsv $WS/hop_topics.tsv %d'
          ' > /tmp/nlogs/rec.log 2>&1 &' % hop_wait)
        A('RECPID=$!')
    for n in plan["nodes"]:
        nid = _slug(n["id"])
        # 先问一句「这个包里到底有哪些可执行」，再决定用哪个名字起。
        # 解析层给的是代码里的节点/类名（Client、Server、talker），不一定等于
        # 包里注册的可执行名（Client 对应的是 client_main）。直接 ros2 run
        # 会得到 `No executable found`，前端显示成「节点一启动就崩」——
        # 线上 80 个节点里 52 个就是这么误判的，跟用户代码没关系。
        A('EXE=$(_pick_exe %s %s %s)'
          % (_sh_quote(n["package"]), _sh_quote(n["entry"]),
             _sh_quote(_base_of(n))))
        A('echo "__EXE__|%s|%s|$EXE"' % (nid, n["entry"]))
        # 名字对不上、包里又只有插件 .so：这是 cpp 组件（composition 的
        # Client/Server/talker 就是），它本来就没有可执行，只能塞进组件容器里加载。
        # 拿 ros2 run 去起必然 No executable found，不该算「你的节点崩了」。
        A('if [ "$EXE" = %s ]; then' % _sh_quote(n["entry"]))
        A('  so=$(ls "install/%s/lib" 2>/dev/null | grep -i %s | grep -ci component)'
          % (n["package"], _sh_quote(n["entry"].split("_")[0])))
        A('  if [ "${so:-0}" -gt 0 ]; then echo "__NOEXE__|%s"; fi' % nid)
        A('fi')
        A('$NB ros2 run %s "$EXE" > /tmp/nlogs/%s.log 2>&1 &'
          % (_sh_quote(n["package"]), nid))
        A('NPIDS="$NPIDS $!"')
        A("NPID2ID[$!]=%s" % nid)
        A("ID2PID[%s]=$!" % nid)
    A("sleep %d" % boot)

    if plan["entries"]:
        A('echo "__STAGE__|inject"')
        A("PPIDS=''")
        for ent in plan["entries"]:
            if not ent.get("type"):
                continue
            A("ros2 topic pub -r %d %s %s %s > /tmp/nlogs/inj_%s.log 2>&1 &"
              % (rate, _sh_quote(ent["topic"]), _sh_quote(ent["type"]),
                 _sh_quote(_fake_value(ent["type"])), _slug(ent["topic"])))
            A('PPIDS="$PPIDS $!"')
        A("sleep 3")

    A('echo "__STAGE__|hop"')
    if plan["hops"]:
        # 统一取证窗口：节点已经跑起来了，这里等一个 hop_wait，让每个话题都有
        # 机会出现消息；然后停掉记录员，让它把发布者数量落盘。
        A('sleep %d' % hop_wait)
        A('if [ -n "${RECPID:-}" ]; then kill -TERM "$RECPID" 2>/dev/null;')
        # 给记录员最多 10 秒写收尾（每个话题的发布者数量），写不完就硬杀 ——
        # 这里绝不能卡住，之前 hop 走完停在收尾、用户干等超时就是这么来的
        A('  for i in $(seq 1 20); do')
        A('    kill -0 "$RECPID" 2>/dev/null || break; sleep 0.5; done')
        A('  kill -9 "$RECPID" 2>/dev/null; wait "$RECPID" 2>/dev/null; fi')
        # 记录员自己挂了=这一轮没有任何证据，宁可报「没取到证」也不能默认为通过
        A('SUB=$(awk -F"\\t" \'$1=="sub"\' /tmp/nlogs/rec.tsv 2>/dev/null | wc -l | tr -d " ")')
        A('GOT=$(awk -F"\\t" \'$1=="got"\' /tmp/nlogs/rec.tsv 2>/dev/null | wc -l | tr -d " ")')
        # 用 __DIAG__ 走（会进结果里的 diag 列表，不跟节点日志抢地方）：
        # 订的是什么类型、哪些订不上，一眼能看全，不用再猜「QoS 不配对」。
        A('echo "__DIAG__|rec 订上 ${SUB:-0} 个话题，收到 ${GOT:-0} 个"')
        A('''awk -F'\t' '$1=="err"{print "__DIAG__|rec-err " $2 " " $3}' /tmp/nlogs/rec.tsv 2>/dev/null | head -n 6''')
        A('''awk -F'\t' '$1=="sub"{print "__DIAG__|rec-sub " $2 " " $3}' /tmp/nlogs/rec.tsv 2>/dev/null | head -n 12''')
        A('''awk -F'\t' '$1=="alt"{print "__DIAG__|rec-alt " $2 " " $3}' /tmp/nlogs/rec.tsv 2>/dev/null | head -n 6''')
        A('echo "[hop] 记录员：订上 ${SUB:-0} 个话题，收到 ${GOT:-0} 个"')
        A('awk -F"\\t" \'$1=="err"{print "[hop] 订不上 " $2 "：" $3}\''
          ' /tmp/nlogs/rec.tsv 2>/dev/null | head -n 6')
        A('if [ ! -s /tmp/nlogs/rec.tsv ]; then')
        A('  echo "---- 记录员没有产出，容器内 Python 环境有问题 ----"')
        A('  tail -n 5 /tmp/nlogs/rec.log 2>/dev/null')
        A('fi')
    for i, h in enumerate(plan["hops"]):
        tag = {"mcu": "MCU", "mcu_out": "MCUB"}.get(h["kind"], "HOP")
        # 对账而不是再订一次：记录员已经替我们订过了，这里只问「这个话题来没来过消息」。
        # 0=来过；1=订上了但窗口内没消息；2=连订阅都没建起来（类型拿不到）
        A("awk -F'\\t' -v t=%s '$1==\"got\"&&$2==t{f=1}"
          " $1==\"sub\"&&$2==t{s=1} $1==\"pub\"&&$2==t{n=$3}"
          " END{exit (f?0:(s?1:2))}' /tmp/nlogs/rec.tsv" % _sh_quote(h["topic"]))
        A("rc=$?")
        # 发布者数量当备注，区分「话题根本没人发」和「发了但我们没收到」
        A("np=$(awk -F'\\t' -v t=%s '$1==\"pub\"&&$2==t{print $3}'"
          " /tmp/nlogs/rec.tsv | tail -n 1)" % _sh_quote(h["topic"]))
        A('[ -z "$np" ] && np="?"')
        A('note="pubs=$np"')
        # 是换 QoS 补订救回来的，就把这件事记在备注里（说明是匹配策略问题，
        # 不是节点没发消息）
        A('''via=$(awk -F'\t' -v t=%s '$1=="got"&&$2==t{print $4}' /tmp/nlogs/rec.tsv 2>/dev/null | tail -n 1)''' % _sh_quote(h["topic"]))
        A('[ -n "$via" ] && note="via=$via"')
        # 取证失败的跳，把它在记录员账本里的几行摊出来：订的是什么类型、有没有
        # 发布者。没有这三行，只能猜「QoS 不配对」还是「话题根本没上线」。
        A('if [ "$rc" -ne 0 ]; then')
        A("  awk -F'\\t' -v t=%s '$2==t{print \"[hop] \" $1 \" \" t \" \" $3}'"
          " /tmp/nlogs/rec.tsv 2>/dev/null | head -n 4" % _sh_quote(h["topic"]))
        A('fi')
        if h["kind"] == "mcu_out":
            # 回传跳多一层判据：消息在线上不算数，得订阅它的节点还活着。
            # 节点要是早崩了，注入的消息没人接，这条「回传」就是假的。
            a = _slug(h.get("to") or "")
            A("alive=0")
            A('p="${ID2PID[%s]:-}"' % a)
            A('if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then alive=1; fi')
            A('if [ "$rc" -eq 2 ]; then echo "__%s_NA__|%s|%s|nosub|%s";'
              ' elif [ "$rc" -eq 0 ] && [ "$alive" -eq 1 ]; then echo '
              '"__%s_OK__|%s|%s|$note|%s"; else echo "__%s_FAIL__|%s|%s|'
              '$note|%s"; fi'
              % (tag, _slug(h["from"]), a, h["topic"],
                 tag, _slug(h["from"]), a, h["topic"],
                 tag, _slug(h["from"]), a, h["topic"]))
        else:
            A('if [ "$rc" -eq 2 ]; then echo "__%s_NA__|%s|%s|nosub|%s";'
              ' elif [ "$rc" -eq 0 ]; then echo "__%s_OK__|%s|%s|$note|%s";'
              ' else echo "__%s_FAIL__|%s|%s|$note|%s"; fi'
              % (tag, _slug(h["from"]), _slug(h["to"]), h["topic"],
                 tag, _slug(h["from"]), _slug(h["to"]), h["topic"],
                 tag, _slug(h["from"]), _slug(h["to"]), h["topic"]))

    A('echo "__STAGE__|done"')
    A('for pid in $PPIDS; do kill $pid 2>/dev/null; done')
    A('for pid in $NPIDS; do kill -INT $pid 2>/dev/null; done')
    A("sleep 3")
    # `ros2 run` 是父进程，SIGINT 未必传给子进程；直接 wait 会永远卡住
    # （线上实测：hop 全跑完、stage=done 之后停在收尾，用户只能干等超时）。
    # 先 SIGKILL 兜底，wait 立刻返回。
    A('for pid in $NPIDS; do kill -9 $pid 2>/dev/null; done')
    A("sleep 1")
    A('for pid in $NPIDS; do')
    A('  wait $pid 2>/dev/null; rc=$?')
    A('  # 137 = 被我们 SIGKILL 收尾，不是节点自己崩，按「中断」记 130')
    A('  [ "$rc" -eq 137 ] && rc=130')
    A('  echo "__NODE_RC__|${NPID2ID[$pid]}|$rc"')
    A("done")
    A('echo "__LOGS_BEGIN__"')
    for n in plan["nodes"]:
        nid = _slug(n["id"])
        A('echo "__NODELOG_BEGIN__|%s"' % nid)
        A("tail -n 200 /tmp/nlogs/%s.log 2>/dev/null" % nid)
        A('echo "__NODELOG_END__|%s"' % nid)
    A('echo "__LOGS_END__"')
    A('echo "__VERIFY_DONE__"')
    A("exit 0")
    return "\n".join(L) + "\n"
