#property copyright "Jose-BF telegram-signal-copier"
#property version   "1.00"
#property strict
#property service

input string InpSymbol = "XAUUSD";
input string InpOutputFile =
   "TelegramSignalCopier\\broker_swap_evidence.csv";
input int InpIntervalSeconds = 60;

bool WriteSnapshot()
  {
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
      return false;
   if(!SymbolSelect(InpSymbol,true))
     {
      PrintFormat("[BrokerMoneySnapshot] SymbolSelect failed: %s error=%d",
                  InpSymbol,GetLastError());
      return false;
     }

   const datetime server_now=TimeTradeServer();
   const datetime gmt_now=TimeGMT();
   MqlTick symbol_tick;
   if(!SymbolInfoTick(InpSymbol,symbol_tick))
      return false;
   const datetime last_server_tick=(datetime)symbol_tick.time;
   if(server_now<=0 || gmt_now<=0 || last_server_tick<=0)
      return false;

   const long server_offset=(long)server_now-(long)gmt_now;
   const long tick_lag=(long)server_now-(long)last_server_tick;
   if(tick_lag<0)
      return false;
   const string temporary=InpOutputFile+".tmp";
   const int flags=FILE_WRITE|FILE_CSV|FILE_ANSI;
   ResetLastError();
   const int handle=FileOpen(temporary,flags,',',CP_UTF8);
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("[BrokerMoneySnapshot] FileOpen failed: error=%d",
                  GetLastError());
      return false;
     }

   FileWrite(
      handle,
      "schema_version",
      "captured_server_epoch",
      "captured_gmt_epoch",
      "last_server_tick_epoch",
      "server_utc_offset_seconds",
      "server_tick_lag_seconds",
      "terminal_build",
      "account_server",
      "instrument_symbol",
      "swap_mode",
      "swap_long",
      "swap_short",
      "swap_rollover3days",
      "point",
      "contract_size",
      "currency_profit",
      "swap_sunday",
      "swap_monday",
      "swap_tuesday",
      "swap_wednesday",
      "swap_thursday",
      "swap_friday",
      "swap_saturday"
   );
   FileWrite(
      handle,
      1,
      (long)server_now,
      (long)gmt_now,
      (long)last_server_tick,
      server_offset,
      tick_lag,
      (long)TerminalInfoInteger(TERMINAL_BUILD),
      AccountInfoString(ACCOUNT_SERVER),
      InpSymbol,
      (long)SymbolInfoInteger(InpSymbol,SYMBOL_SWAP_MODE),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_SWAP_LONG),8),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_SWAP_SHORT),8),
      (long)SymbolInfoInteger(InpSymbol,SYMBOL_SWAP_ROLLOVER3DAYS),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_POINT),12),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_TRADE_CONTRACT_SIZE),8),
      SymbolInfoString(InpSymbol,SYMBOL_CURRENCY_PROFIT),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_SWAP_SUNDAY),8),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_SWAP_MONDAY),8),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_SWAP_TUESDAY),8),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_SWAP_WEDNESDAY),8),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_SWAP_THURSDAY),8),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_SWAP_FRIDAY),8),
      DoubleToString(SymbolInfoDouble(InpSymbol,SYMBOL_SWAP_SATURDAY),8)
   );
   FileFlush(handle);
   FileClose(handle);

   ResetLastError();
   if(!FileMove(
      temporary,
      0,
      InpOutputFile,
      FILE_REWRITE))
     {
      PrintFormat("[BrokerMoneySnapshot] FileMove failed: error=%d",
                  GetLastError());
      return false;
     }
   return true;
  }

void OnStart()
  {
   const int sleep_ms=(int)MathMax(1000,InpIntervalSeconds*1000);
   bool previous_success=false;
   while(!IsStopped())
     {
      const bool success=WriteSnapshot();
      if(success && !previous_success)
         Print("[BrokerMoneySnapshot] Native broker evidence active.");
      if(!success && previous_success)
         Print("[BrokerMoneySnapshot] Native broker evidence unavailable.");
      previous_success=success;
      Sleep(sleep_ms);
     }
  }
