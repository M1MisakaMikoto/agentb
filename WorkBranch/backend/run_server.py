import sys
import os
import time
import signal
import logging
import traceback
import threading
import gc

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

print("Starting server...", flush=True)

import uvicorn
from app import app

print("App imported", flush=True)

_start_time = time.time()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('server_detailed.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

MAX_RESTART_COUNT = 5
RESTART_DELAY = 3

MEMORY_WARNING_THRESHOLD_MB = 800
MEMORY_CRITICAL_THRESHOLD_MB = 1200
MEMORY_CHECK_INTERVAL_SEC = 30
MAX_MEMORY_MB = 1500


def memory_monitor_thread():
    """后台线程：定期监控内存使用情况"""
    logger.info(f"✅ 内存监控线程已启动 (检查间隔: {MEMORY_CHECK_INTERVAL_SEC}s)")
    
    while True:
        try:
            import psutil
            
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            
            if memory_mb > MEMORY_CRITICAL_THRESHOLD_MB:
                logger.critical(f"⚠️ 内存危险: {memory_mb:.0f}MB > {MEMORY_CRITICAL_THRESHOLD_MB}MB")
                logger.critical("执行紧急垃圾回收...")
                
                collected = gc.collect()
                
                memory_after = process.memory_info().rss / 1024 / 1024
                logger.info(f"GC 后: {memory_after:.0f}MB (回收: {memory_mb - memory_after:.0f}MB, 对象: {collected})")
                
                if memory_after > MAX_MEMORY_MB:
                    logger.error(f"❌ 内存超出硬限制 {MAX_MEMORY_MB}MB (当前: {memory_after:.0f}MB)")
                    logger.error("建议: 立即重启服务或优化内存使用")
                    
            elif memory_mb > MEMORY_WARNING_THRESHOLD_MB:
                logger.warning(f"⚠️ 内存偏高: {memory_mb:.0f}MB > {MEMORY_WARNING_THRESHOLD_MB}MB")
                collected = gc.collect()
                if collected > 0:
                    logger.info(f"预防性 GC 回收了 {collected} 个对象")
                    
            else:
                logger.debug(f"✅ 内存正常: {memory_mb:.0f}MB")
                
        except ImportError:
            logger.debug("psutil 未安装，跳过内存监控")
        except Exception as e:
            logger.error(f"内存监控异常: {e}")
        
        time.sleep(MEMORY_CHECK_INTERVAL_SEC)


def setup_signal_handlers():
    """配置信号处理器以支持优雅关闭（用于独立启动模式）"""
    logger.info("配置信号处理器...")
    
    def on_shutdown_signal(signum, frame):
        """
        处理关闭信号（SIGINT/SIGTERM/SIGBREAK）
        当用户关闭 CMD 窗口或按 Ctrl+C 时触发
        """
        import datetime
        
        signal_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
        
        logger.warning("=" * 60)
        logger.warning(f"📡 接收到关闭信号: {signal_name} ({signum})")
        logger.warning(f"   时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            mem_mb = proc.memory_info().rss / 1024 / 1024
            logger.warning(f"   当前内存: {mem_mb:.1f}MB")
            logger.warning(f"   活跃线程: {threading.active_count()}")
        except ImportError:
            pass
        
        logger.warning("   正在通知 Uvicorn 优雅停止...")
        logger.warning("=" * 60)
    
    def on_force_exit(signum, frame):
        """处理强制退出信号（第二次 SIGINT）"""
        logger.critical("=" * 60)
        logger.critical("⚠️  收到强制退出信号！立即终止...")
        logger.critical("=" * 60)
        sys.exit(1)
    
    signals_configured = []
    
    try:
        signal.signal(signal.SIGINT, on_shutdown_signal)
        signals_configured.append('SIGINT')
    except (OSError, ValueError) as e:
        logger.warning(f"无法配置 SIGINT: {e}")
    
    try:
        signal.signal(signal.SIGTERM, on_shutdown_signal)
        signals_configured.append('SIGTERM')
    except (OSError, ValueError) as e:
        logger.warning(f"无法配置 SIGTERM: {e}")
    
    # Windows 特有：处理 CTRL+BREAK（窗口关闭时发送）
    if hasattr(signal, 'SIGBREAK'):
        try:
            signal.signal(signal.SIGBREAK, on_shutdown_signal)
            signals_configured.append('SIGBREAK')
        except (OSError, ValueError) as e:
            logger.warning(f"无法配置 SIGBREAK: {e}")
    
    if signals_configured:
        logger.info(f"✅ 已配置信号处理器: {', '.join(signals_configured)}")
        logger.info("   关闭窗口或按 Ctrl+C 可优雅停止服务")
    else:
        logger.warning("⚠️  未成功配置任何信号处理器")


def run_server():
    """运行服务器单次实例"""
    setup_signal_handlers()
    
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
        access_log=True,
        timeout_keep_alive=300,
    )


def main():
    """主进程守护循环"""
    restart_count = 0
    
    logger.info("=" * 60)
    logger.info("Backend Server Starting")
    logger.info(f"Python: {sys.version}")
    logger.info(f"Working Directory: {os.getcwd()}")
    logger.info(f"内存管理配置:")
    logger.info(f"  - 预警阈值: {MEMORY_WARNING_THRESHOLD_MB}MB")
    logger.info(f"  - 危险阈值: {MEMORY_CRITICAL_THRESHOLD_MB}MB")
    logger.info(f"  - 硬性上限: {MAX_MEMORY_MB}MB")
    logger.info(f"  - 检查间隔: {MEMORY_CHECK_INTERVAL_SEC}s")
    logger.info("=" * 60)
    
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    monitor_thread = threading.Thread(target=memory_monitor_thread, daemon=True)
    monitor_thread.start()
    
    while restart_count < MAX_RESTART_COUNT:
        try:
            logger.info(f"启动服务器实例 (重启次数: {restart_count}/{MAX_RESTART_COUNT})")
            
            try:
                import psutil
                process = psutil.Process(os.getpid())
                memory_mb = process.memory_info().rss / 1024 / 1024
                logger.info(f"启动时内存使用: {memory_mb:.1f}MB")
            except ImportError:
                pass
            
            run_server()
            
            logger.info("服务器正常退出")
            break
            
        except KeyboardInterrupt:
            logger.info("用户中断，退出")
            break
            
        except Exception as e:
            restart_count += 1
            logger.error(f"服务器异常退出 (#{restart_count}): {e}")
            logger.error(f"详细错误:\n{traceback.format_exc()}")
            
            if restart_count >= MAX_RESTART_COUNT:
                logger.error(f"达到最大重启次数 ({MAX_RESTART_COUNT})，停止尝试")
                break
            
            logger.info("执行紧急内存释放...")
            gc.collect()
            
            logger.info(f"等待 {RESTART_DELAY} 秒后重启...")
            time.sleep(RESTART_DELAY)
    
    uptime_seconds = int(time.time() - _start_time)
    logger.info("=" * 60)
    logger.info(f"进程守护结束，总运行时间: {uptime_seconds}秒")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
