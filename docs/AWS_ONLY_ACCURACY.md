# Can AWS alone reach 99 %? Measured on held-out public test crops

Same images for every method. Attack error = APCER (attack accepted as live). Live error = BPCER (live rejected).

## ALL

| Method | Attack accepted (APCER) | Live rejected (BPCER) | Accuracy |
|---|---|---|---|
| aws_labels_orig | 91.4% [89.4-93.1] (n=865) | 9.3% [7.0-12.3] (n=464) | 37.2% |
| aws_labels_narrow | 95.4% [93.8-96.6] (n=865) | 0.2% [0.0-1.2] (n=464) | 37.8% |
| aws_labels_any | 58.2% [54.8-61.4] (n=865) | 23.7% [20.1-27.8] (n=464) | 53.9% |
| b_cnn | 3.0% [2.1-4.4] (n=865) | 5.2% [3.5-7.6] (n=464) | 96.2% |
| b_cnn+labels | 2.9% [2.0-4.2] (n=865) | 5.4% [3.7-7.8] (n=464) | 96.2% |

## casia

| Method | Attack accepted (APCER) | Live rejected (BPCER) | Accuracy |
|---|---|---|---|
| aws_labels_orig | 94.7% [92.5-96.2] (n=580) | 23.2% [17.7-29.9] (n=181) | 22.3% |
| aws_labels_narrow | 97.4% [95.8-98.4] (n=580) | 0.0% [0.0-2.1] (n=181) | 25.8% |
| aws_labels_any | 53.1% [49.0-57.1] (n=580) | 58.0% [50.7-65.0] (n=181) | 45.7% |
| b_cnn | 0.0% [0.0-0.7] (n=580) | 6.6% [3.8-11.2] (n=181) | 98.4% |
| b_cnn+labels | 0.0% [0.0-0.7] (n=580) | 6.6% [3.8-11.2] (n=181) | 98.4% |

## celeba

| Method | Attack accepted (APCER) | Live rejected (BPCER) | Accuracy |
|---|---|---|---|
| aws_labels_orig | 84.9% [80.3-88.6] (n=285) | 0.4% [0.1-2.0] (n=283) | 57.2% |
| aws_labels_narrow | 91.2% [87.4-94.0] (n=285) | 0.4% [0.1-2.0] (n=283) | 54.0% |
| aws_labels_any | 68.4% [62.8-73.5] (n=285) | 1.8% [0.8-4.1] (n=283) | 64.8% |
| b_cnn | 9.1% [6.3-13.0] (n=285) | 4.2% [2.4-7.3] (n=283) | 93.3% |
| b_cnn+labels | 8.8% [6.0-12.6] (n=285) | 4.6% [2.7-7.7] (n=283) | 93.3% |

Rekognition Face Liveness (managed Tier 3) is NOT in this table: it needs the browser video widget and cannot be run headless, so its accuracy is unmeasured here and any vendor figure must not be quoted as ours.