#!/bin/bash
# Please run this script at the root dir of cords using "bash ./scripts/run_tabular_dataloader_batch.sh"

start=$(date +%s)
log_path="./scripts/BATCH_PROCESS_$start"
script_path="./scripts/run_nlp_dataloader.py"
mkdir -p $log_path

#datasets=("corona" "news" "twitter")
#datasets=("corona")
datasets=("corona" "twitter" "ag_news")
#strategies=("glister" "random-ol" "full" "random" "facloc" "graphcut" "sumredun" "satcov" "CRAIG")
#select_ratios=("0.1" "0.2" "0.3")
strategies=("random" "facloc" "graphcut" "sumredun" "satcov")
select_ratios=("0.1" "0.3" "0.5")
models=("LSTM")
device="cuda"

pid=()
for select_ratio in "${select_ratios[@]}"; do
  for model in "${models[@]}"; do
    for dataset in "${datasets[@]}"; do
      for strategy in "${strategies[@]}"; do
        echo "Running dataset: $dataset with strategy: $strategy and select_ratio: $select_ratio... "
        #      python3 $script_path --dataset $dataset --dss_strategy $strategy --device $device --model $model 1>$log_path/${model}_${dataset}_$strategy.log 2>$log_path/${model}_${dataset}_$strategy.err &
        python3 $script_path --dataset $dataset --dss_strategy $strategy --device $device \
          --model $model --select_ratio $select_ratio \
          1>$log_path/${model}_${dataset}_$strategy.log 2>$log_path/${model}_${dataset}_$strategy.err
        #      _pid=$!
        #      pid+=($_pid)
        #      for _pid in "${pid[@]}"; do
        #        wait $_pid
        #      done
      done
      pid=()
    done
  done
done

kill_scripts() {
  echo "Killing processes... "
  for _pid in "${pid[@]}"; do
    kill -s SIGTERM $_pid >/dev/null 2>&1 || (sleep 10 && kill -9 $_pid >/dev/null 2>&1 &)
  done
}

trap_quit() {
  kill_scripts
}

trap trap_quit EXIT
