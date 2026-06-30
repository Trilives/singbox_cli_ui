"""事务 / 回退引擎：让整个配置流程可受控中止并回退已应用的改动。

用法：
    with Transaction("初始化") as t:
        t.backup_file(paths.CONFIG_FILE)        # 改文件前先登记快照
        write_config(...)
        t.add_undo("卸载服务", lambda: service.remove(name))
        service.install(name)
        ...
    # 正常走完 → 自动 commit（清空，不回退）
    # 中途抛 Cancelled / 任何异常 → 自动按 LIFO 回退已登记的 undo

设计要点：
- backup_file：记录目标文件改动前的内容（或"原本不存在"），回退时还原/删除。
- track_path：登记一个将被创建的路径，回退时若它原本不存在则删除。
- add_undo：登记任意自定义回退动作（如卸载服务、还原 active 指针）。
- 回退按登记的逆序执行；单个 undo 失败不阻断其余，最后汇总报告。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from . import shell
from .errors import Cancelled

UndoFn = Callable[[], None]


class Transaction:
    def __init__(self, name: str):
        self.name = name
        self._undos: list[tuple[str, UndoFn]] = []
        self._committed = False

    # -- 登记回退动作 ------------------------------------------------------- #
    def add_undo(self, desc: str, fn: UndoFn) -> None:
        self._undos.append((desc, fn))

    def backup_file(self, path: Path) -> None:
        """在修改/创建 path 前调用，登记回退到当前状态。"""
        path = Path(path)
        if path.exists():
            data = path.read_bytes()
            mode = path.stat().st_mode

            def _restore() -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(mode)

            self.add_undo(f"还原文件 {path}", _restore)
        else:
            def _remove() -> None:
                if path.exists():
                    path.unlink()

            self.add_undo(f"删除新建文件 {path}", _remove)

    def track_path(self, path: Path) -> None:
        """登记一个将被创建的文件/目录；回退时若原本不存在则删除。"""
        path = Path(path)
        if path.exists():
            return  # 已存在则不归我们删除，避免误删

        def _remove() -> None:
            if not path.exists():
                return
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

        self.add_undo(f"删除新建路径 {path}", _remove)

    # -- 提交 / 回退 -------------------------------------------------------- #
    def commit(self) -> None:
        self._committed = True
        self._undos.clear()

    def rollback(self) -> None:
        if not self._undos:
            return
        shell.warn(f"正在回退「{self.name}」已应用的改动…")
        errors = 0
        for desc, fn in reversed(self._undos):
            try:
                fn()
                shell.info(f"  已回退: {desc}")
            except Exception as exc:  # noqa: BLE001 - 回退要尽力而为
                errors += 1
                shell.error(f"  回退失败: {desc} ({exc})")
        self._undos.clear()
        if errors:
            shell.error(f"回退完成，但有 {errors} 项失败，请手动检查。")
        else:
            shell.ok("已回退到操作前状态。")

    # -- 上下文管理 -------------------------------------------------------- #
    def __enter__(self) -> "Transaction":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
            return False
        if issubclass(exc_type, Cancelled):
            shell.warn(f"已取消「{self.name}」。")
            self.rollback()
            return True  # 吞掉 Cancelled，回到上层菜单
        # 其他异常：先回退，再向上抛出
        shell.error(f"「{self.name}」出错：{exc}")
        self.rollback()
        return False
