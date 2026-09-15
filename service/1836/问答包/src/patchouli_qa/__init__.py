"""开箱即用的受限沙箱问答引擎（静态数据分析）。

对外只有::

    from patchouli_qa import PatchouliQA

    qa = PatchouliQA.init(root_path)
    result = qa.invoke(question, data_constraint=["vanilla"], output_log=log_dir)
"""

from .api import InvokeResult, PatchouliQA

__all__ = ["PatchouliQA", "InvokeResult"]
__version__ = "0.1.0"
