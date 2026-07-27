#property strict
#property version   "1.00"
#property description "Tester-only virtual replay for executed Telegram signals"

input string InpFixtureFile = "TelegramSignalReplay\\fixture.csv";
input string InpResultFile = "TelegramSignalReplay\\result.csv";
input string InpPolicy = "observed_close";
input string InpFixtureSha256 = "";

struct ReplayTicket
  {
   int               schema_version;
   string            signal_id;
   string            provider;
   ulong             ticket;
   string            direction;
   double            volume;
   long              entry_time_msc;
   double            entry_price;
   long              observed_close_time_msc;
   double            observed_close_price;
   string            observed_close_reason;
   double            observed_pnl_eur;
   double            provider_sl;
   double            provider_tp1;
   double            provider_tp2;
   string            source_sha256;
   bool              opened;
   bool              closed;
   bool              be_active;
   string            status;
   long              close_time_msc;
   double            close_price;
   string            close_reason;
   double            pnl_eur;
   double            touch_bid;
   double            touch_ask;
  };

ReplayTicket g_tickets[];
int          g_result_handle=INVALID_HANDLE;
bool         g_finalized=false;
MqlTick      g_last_tick={};

bool IsSupportedPolicy()
  {
   return(
      InpPolicy=="observed_close"
      || InpPolicy=="all_tp2_keep_be"
      || InpPolicy=="all_tp2_no_be"
   );
  }

bool ReadHeader(const int handle)
  {
   const string expected[]=
     {
      "schema_version",
      "signal_id",
      "provider",
      "ticket",
      "direction",
      "volume",
      "entry_time_msc",
      "entry_price",
      "observed_close_time_msc",
      "observed_close_price",
      "observed_close_reason",
      "observed_pnl_eur",
      "provider_sl",
      "provider_tp1",
      "provider_tp2",
      "source_sha256"
     };
   for(int index=0; index<ArraySize(expected); index++)
     {
      if(FileIsEnding(handle))
         return(false);
      if(FileReadString(handle)!=expected[index])
         return(false);
     }
   return(true);
  }

bool ValidTicket(const ReplayTicket &item)
  {
   if(item.schema_version!=1 || item.signal_id=="" || item.ticket==0)
      return(false);
   if(item.direction!="BUY" && item.direction!="SELL")
      return(false);
   if(item.volume<=0.0 || item.entry_time_msc<=0 || item.entry_price<=0.0)
      return(false);
   if(item.observed_close_time_msc<item.entry_time_msc)
      return(false);
   if(item.observed_close_price<=0.0 || item.provider_sl<=0.0)
      return(false);
   if(item.provider_tp1<=0.0 || item.provider_tp2<=0.0)
      return(false);
   if(StringLen(item.source_sha256)!=64)
      return(false);
   return(true);
  }

bool LoadFixture()
  {
   const int handle=FileOpen(
      InpFixtureFile,
      FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON,
      ';',
      CP_UTF8
   );
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("Fixture open failed: %s error=%d",InpFixtureFile,GetLastError());
      return(false);
     }
   if(!ReadHeader(handle))
     {
      Print("Fixture header is invalid");
      FileClose(handle);
      return(false);
     }

   while(!FileIsEnding(handle))
     {
      string schema=FileReadString(handle);
      if(schema=="")
         break;
      ReplayTicket item={};
      item.schema_version=(int)StringToInteger(schema);
      item.signal_id=FileReadString(handle);
      item.provider=FileReadString(handle);
      item.ticket=(ulong)StringToInteger(FileReadString(handle));
      item.direction=FileReadString(handle);
      item.volume=StringToDouble(FileReadString(handle));
      item.entry_time_msc=StringToInteger(FileReadString(handle));
      item.entry_price=StringToDouble(FileReadString(handle));
      item.observed_close_time_msc=StringToInteger(FileReadString(handle));
      item.observed_close_price=StringToDouble(FileReadString(handle));
      item.observed_close_reason=FileReadString(handle);
      item.observed_pnl_eur=StringToDouble(FileReadString(handle));
      item.provider_sl=StringToDouble(FileReadString(handle));
      item.provider_tp1=StringToDouble(FileReadString(handle));
      item.provider_tp2=StringToDouble(FileReadString(handle));
      item.source_sha256=FileReadString(handle);
      item.status="pending";
      if(!ValidTicket(item))
        {
         PrintFormat("Invalid fixture ticket: %I64u",item.ticket);
         FileClose(handle);
         return(false);
        }
      const int size=ArraySize(g_tickets);
      ArrayResize(g_tickets,size+1);
      g_tickets[size]=item;
     }
   FileClose(handle);
   return(ArraySize(g_tickets)>0);
  }

bool OpenResult()
  {
   g_result_handle=FileOpen(
      InpResultFile,
      FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,
      ';',
      CP_UTF8
   );
   if(g_result_handle==INVALID_HANDLE)
     {
      PrintFormat("Result open failed: %s error=%d",InpResultFile,GetLastError());
      return(false);
     }
   FileWrite(
      g_result_handle,
      "schema_version",
      "policy_id",
      "signal_id",
      "ticket",
      "status",
      "direction",
      "volume",
      "entry_time_msc",
      "entry_price",
      "close_time_msc",
      "close_price",
      "close_reason",
      "pnl_eur",
      "touch_bid",
      "touch_ask",
      "source_sha256"
   );
   return(true);
  }

void BlockTicket(ReplayTicket &item,const string reason,const MqlTick &tick)
  {
   item.closed=true;
   item.status="blocked";
   item.close_reason=reason;
   item.close_time_msc=tick.time_msc;
   item.touch_bid=tick.bid;
   item.touch_ask=tick.ask;
  }

bool CloseVirtual(
   ReplayTicket &item,
   const long close_time_msc,
   const double close_price,
   const string close_reason,
   const MqlTick &tick
)
  {
   const ENUM_ORDER_TYPE order_type=(
      item.direction=="BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL
   );
   double pnl=0.0;
   ResetLastError();
   if(!OrderCalcProfit(
      order_type,
      _Symbol,
      item.volume,
      item.entry_price,
      close_price,
      pnl
   ))
     {
      BlockTicket(item,"order_calc_profit_failed",tick);
      PrintFormat(
         "OrderCalcProfit failed ticket=%I64u error=%d",
         item.ticket,
         GetLastError()
      );
      return(false);
     }
   item.closed=true;
   item.status="closed";
   item.close_time_msc=close_time_msc;
   item.close_price=close_price;
   item.close_reason=close_reason;
   item.pnl_eur=NormalizeDouble(
      pnl,
      (int)AccountInfoInteger(ACCOUNT_CURRENCY_DIGITS)
   );
   item.touch_bid=tick.bid;
   item.touch_ask=tick.ask;
   return(true);
  }

bool ProfitLevelTouched(
   const ReplayTicket &item,
   const double side,
   const double level
)
  {
   if(item.direction=="BUY")
      return(side>=level);
   return(side<=level);
  }

bool StopLevelTouched(
   const ReplayTicket &item,
   const double side,
   const double level
)
  {
   if(item.direction=="BUY")
      return(side<=level);
   return(side>=level);
  }

void ProcessObservedClose(ReplayTicket &item,const MqlTick &tick)
  {
   if(tick.time_msc<item.observed_close_time_msc)
      return;
   CloseVirtual(
      item,
      item.observed_close_time_msc,
      item.observed_close_price,
      item.observed_close_reason,
      tick
   );
  }

void ProcessTp2Policy(ReplayTicket &item,const MqlTick &tick)
  {
   const double side=(item.direction=="BUY" ? tick.bid : tick.ask);
   if(side<=0.0)
     {
      BlockTicket(item,"invalid_quote",tick);
      return;
     }
   const double active_sl=(item.be_active ? item.entry_price : item.provider_sl);
   const bool hit_tp=ProfitLevelTouched(item,side,item.provider_tp2);
   const bool hit_sl=StopLevelTouched(item,side,active_sl);
   if(hit_tp && hit_sl)
     {
      BlockTicket(item,"same_tick_tp_sl_ambiguity",tick);
      return;
     }
   if(hit_tp)
     {
      CloseVirtual(item,tick.time_msc,item.provider_tp2,"tp2",tick);
      return;
     }
   if(hit_sl)
     {
      CloseVirtual(
         item,
         tick.time_msc,
         side,
         (item.be_active ? "be" : "sl"),
         tick
      );
      return;
     }
   if(
      InpPolicy=="all_tp2_keep_be"
      && !item.be_active
      && ProfitLevelTouched(item,side,item.provider_tp1)
   )
      item.be_active=true;
  }

bool AllTicketsFinished()
  {
   for(int index=0; index<ArraySize(g_tickets); index++)
     {
      if(!g_tickets[index].closed)
         return(false);
     }
   return(true);
  }

void FinalizeResults()
  {
   if(g_finalized)
      return;
   if(g_result_handle==INVALID_HANDLE)
      return;
   for(int index=0; index<ArraySize(g_tickets); index++)
     {
      ReplayTicket item=g_tickets[index];
      if(!item.closed)
         BlockTicket(item,"horizon_open",g_last_tick);
      FileWrite(
         g_result_handle,
         item.schema_version,
         InpPolicy,
         item.signal_id,
         (string)item.ticket,
         item.status,
         item.direction,
         DoubleToString(item.volume,2),
         IntegerToString(item.entry_time_msc),
         DoubleToString(item.entry_price,_Digits),
         IntegerToString(item.close_time_msc),
         DoubleToString(item.close_price,_Digits),
         item.close_reason,
         DoubleToString(item.pnl_eur,2),
         DoubleToString(item.touch_bid,_Digits),
         DoubleToString(item.touch_ask,_Digits),
         item.source_sha256
      );
     }
   FileFlush(g_result_handle);
   g_finalized=true;
   PrintFormat(
      "TelegramSignalReplay complete policy=%s tickets=%d fixture=%s",
      InpPolicy,
      ArraySize(g_tickets),
      InpFixtureSha256
   );
  }

int OnInit()
  {
   if(!MQLInfoInteger(MQL_TESTER))
     {
      Print("TelegramSignalReplayEA is tester-only");
      return(INIT_FAILED);
     }
   if(!IsSupportedPolicy() || InpFixtureSha256=="")
      return(INIT_PARAMETERS_INCORRECT);
   if(!LoadFixture() || !OpenResult())
      return(INIT_FAILED);
   return(INIT_SUCCEEDED);
  }

void OnTick()
  {
   MqlTick tick={};
   if(!SymbolInfoTick(_Symbol,tick))
      return;
   g_last_tick=tick;
   for(int index=0; index<ArraySize(g_tickets); index++)
     {
      if(g_tickets[index].closed)
         continue;
      if(!g_tickets[index].opened)
        {
         if(tick.time_msc<g_tickets[index].entry_time_msc)
            continue;
         g_tickets[index].opened=true;
         g_tickets[index].status="open";
        }
      if(InpPolicy=="observed_close")
         ProcessObservedClose(g_tickets[index],tick);
      else
         ProcessTp2Policy(g_tickets[index],tick);
     }
   if(AllTicketsFinished())
      FinalizeResults();
  }

void OnDeinit(const int reason)
  {
   FinalizeResults();
   if(g_result_handle!=INVALID_HANDLE)
     {
      FileClose(g_result_handle);
      g_result_handle=INVALID_HANDLE;
     }
  }
