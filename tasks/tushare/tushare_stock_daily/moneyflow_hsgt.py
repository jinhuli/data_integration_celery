"""
Created on 2018/9/7
@author: yby
@desc    : 2018-09-7可正常运行
contact author:ybychem@gmail.com
"""
import pandas as pd
import logging
from tasks.backend.orm import build_primary_key
from datetime import date, datetime, timedelta
from tasks.utils.fh_utils import try_2_date, STR_FORMAT_DATE, datetime_2_str, split_chunk, try_n_times
from tasks import app
from sqlalchemy.types import String, Date, Integer
from sqlalchemy.dialects.mysql import DOUBLE
from tasks.backend import engine_md
from tasks.merge.code_mapping import update_from_info_table
from tasks.utils.db_utils import with_db_session, add_col_2_table, alter_table_2_myisam, \
    bunch_insert_on_duplicate_update
from tasks.tushare.ts_pro_api import pro
from tasks.config import config

DEBUG = False
logger = logging.getLogger()
DATE_BASE = datetime.strptime('2005-01-01', STR_FORMAT_DATE).date()
ONE_DAY = timedelta(days=1)
# 标示每天几点以后下载当日行情数据
BASE_LINE_HOUR = 16
STR_FORMAT_DATE_TS = '%Y%m%d'


@try_n_times(times=5, sleep_time=0, exception_sleep_time=60)
def invoke_moneyflow_hsgt(trade_date):
    moneyflow_hsgt = pro.moneyflow_hsgt(trade_date=trade_date)
    return moneyflow_hsgt


@app.task
def import_tushare_moneyflow_hsgt(chain_param=None):
    """
    插入股票日线数据到最近一个工作日-1。
    如果超过 BASE_LINE_HOUR 时间，则获取当日的数据
    :return:
    """
    table_name = 'tushare_moneyflow_hsgt'
    logging.info("更新 %s 开始", table_name)
    param_list = [
        ('trade_date', Date),
        ('ggt_ss', DOUBLE),
        ('ggt_sz', DOUBLE),
        ('hgt', DOUBLE),
        ('sgt', DOUBLE),
        ('north_money', DOUBLE),
        ('south_money', DOUBLE),

    ]

    has_table = engine_md.has_table(table_name)
    # 进行表格判断，确定是否含有tushare_daily_basic

    # 下面一定要注意引用表的来源，否则可能是串，提取混乱！！！比如本表是tushare_daily_basic，所以引用的也是这个，如果引用错误，就全部乱了l
    if has_table:
        sql_str = """
               select cal_date            
               FROM
                (
                 select * from tushare_trade_date trddate 
                 where( cal_date>(SELECT max(trade_date) FROM  {table_name}))
               )tt
               where (is_open=1 
                      and cal_date <= if(hour(now())<16, subdate(curdate(),1), curdate()) 
                      and exchange_id='SSE') """.format(table_name=table_name)
    else:
        sql_str = """
               SELECT cal_date FROM tushare_trade_date trddate WHERE (trddate.is_open=1 
            AND cal_date <= if(hour(now())<16, subdate(curdate(),1), curdate()) 
            AND exchange_id='SSE'  AND cal_date>='2014-11-17') ORDER BY cal_date"""
        logger.warning('%s 不存在，仅使用 tushare_trade_date 表进行计算日期范围', table_name)

    with with_db_session(engine_md) as session:
        # 获取交易日数据
        table = session.execute(sql_str)
        trddate = list(row[0] for row in table.fetchall())
    # 设置 dtype
    dtype = {key: val for key, val in param_list}

    try:
        for i in range(len(trddate)):
            trade_date = datetime_2_str(trddate[i], STR_FORMAT_DATE_TS)
            data_df = invoke_moneyflow_hsgt(trade_date=trade_date)
            if len(data_df) > 0:
                data_count = bunch_insert_on_duplicate_update(
                    data_df, table_name, engine_md, dtype,
                    myisam_if_create_table=True, primary_keys=['trade_date'], schema=config.DB_SCHEMA_MD)
                logging.info("%s更新 %s 结束 %d 条信息被更新", trade_date, table_name, data_count)
            else:
                logging.info("无数据信息可被更新")
    finally:
        if not has_table and engine_md.has_table(table_name):
            alter_table_2_myisam(engine_md, [table_name])
            # build_primary_key([table_name])
            create_pk_str = """ALTER TABLE {table_name}
                CHANGE COLUMN `trade_date` `trade_date` VARCHAR(20) NOT NULL FIRST,
                ADD PRIMARY KEY (`trade_date`)""".format(table_name=table_name)
            with with_db_session(engine_md) as session:
                session.execute(create_pk_str)
            logger.info('%s 表  `trade_date` 主键设置完成', table_name)


if __name__ == "__main__":
    # DEBUG = True
    import_tushare_moneyflow_hsgt()
