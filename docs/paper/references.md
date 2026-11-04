# References for the related-work section

**How to read this list:**
- Every entry is a work I am confident exists. Nothing is listed only to fill a gap.
- Entries marked **checked** had their authors, year and venue confirmed with a web search on 4 Nov 2026; the source is
  given.
- All other entries come from memory of well-known papers. They carry **verify before citing**: the work is real, but
  its volume, issue and exact title must be checked against the publisher before use.
- No DOIs or page numbers are given unless a check returned them.
- Where I know of no reliable reference (free-data quality for Indian equities, Yahoo Finance accuracy), the gap is
  listed at the end instead of a guess.

## Data validation and data-quality assessment

1. Wang, R. Y., & Strong, D. M. (1996). Beyond accuracy: What data quality means to data consumers. *Journal of
   Management Information Systems*, 12(4). **verify before citing**
2. Batini, C., Cappiello, C., Francalanci, C., & Maurino, A. (2009). Methodologies for data quality assessment and
   improvement. *ACM Computing Surveys*, 41(3). **verify before citing**
3. Schelter, S., Lange, D., Schmidt, P., Celikel, M., Biessmann, F., & Grafberger, A. (2018). Automating large-scale
   data quality verification. *Proceedings of the VLDB Endowment*, 11(12). **verify before citing**
4. Breck, E., Polyzotis, N., Roy, S., Whang, S. E., & Zinkevich, M. (2019). Data validation for machine learning.
   *Proceedings of SysML (now MLSys)*. **verify before citing**
5. Polyzotis, N., Roy, S., Whang, S. E., & Zinkevich, M. (2018). Data lifecycle challenges in production machine
   learning: A survey. *SIGMOD Record*, 47(2), 17–28. doi:10.1145/3299887.3299891. **checked**
   ([SIGMOD Record](https://sigmodrecord.org/2018/10/19/data-lifecycle-challenges-in-production-machine-learning-a-survey/))

## Error detection and data repair

6. Rahm, E., & Do, H. H. (2000). Data cleaning: Problems and current approaches. *IEEE Data Engineering Bulletin*,
   23(4). **verify before citing**
7. Hellerstein, J. M. (2008). *Quantitative data cleaning for large databases*. Technical report, UC Berkeley, written
   under contract to UNECE. **checked**
   ([PDF copy](https://biblioteca.unisced.edu.mz/bitstream/123456789/2671/1/Quantitative-Data-Cleaning-for-Large-Databases.pdf))
8. Fan, W., Geerts, F., Jia, X., & Kementsietsidis, A. (2008). Conditional functional dependencies for capturing data
   inconsistencies. *ACM Transactions on Database Systems*, 33(2). **verify before citing**
9. Chu, X., Ilyas, I. F., & Papotti, P. (2013). Holistic data cleaning: Putting violations into context. *ICDE 2013*,
   458–469. doi:10.1109/ICDE.2013.6544847. **checked** ([dblp](https://dblp1.uni-trier.de/rec/conf/icde/ChuIP13.html))
10. Abedjan, Z., Chu, X., Deng, D., Fernandez, R. C., Ilyas, I. F., Ouzzani, M., Papotti, P., Stonebraker, M., & Tang,
    N. (2016). Detecting data errors: Where are we and what needs to be done? *PVLDB*, 9(12). **verify before citing**
11. Krishnan, S., Wang, J., Wu, E., Franklin, M. J., & Goldberg, K. (2016). ActiveClean: Interactive data cleaning for
    statistical modeling. *PVLDB*, 9(12). **verify before citing**
12. Rekatsinas, T., Chu, X., Ilyas, I. F., & Ré, C. (2017). HoloClean: Holistic data repairs with probabilistic
    inference. *PVLDB*, 10(11). **verify before citing**
13. Ilyas, I. F., & Chu, X. (2019). *Data Cleaning*. ACM Books. **verify before citing**

## Temporal databases, versioning and provenance

14. Snodgrass, R. T. (1999). *Developing Time-Oriented Database Applications in SQL*. Morgan Kaufmann. **checked**
    ([bibsonomy record](https://www.bibsonomy.org/bibtex/70491c3ed3754a11c793c51dc5d9733e))
15. Jensen, C. S., & Snodgrass, R. T. (1999). Temporal data management. *IEEE Transactions on Knowledge and Data
    Engineering*, 11(1). **verify before citing**
16. Kulkarni, K., & Michels, J.-E. (2012). Temporal features in SQL:2011. *SIGMOD Record*, 41(3).
    **verify before citing**
17. Johnston, T. (2014). *Bitemporal Data: Theory and Practice*. Morgan Kaufmann. ISBN 9780124080676. **checked**
    ([Tenlong listing](https://www.tenlong.com.tw/products/9780124080676))
18. Buneman, P., Khanna, S., & Tan, W.-C. (2001). Why and where: A characterization of data provenance. *ICDT 2001*.
    **verify before citing**
19. Green, T. J., Karvounarakis, G., & Tannen, V. (2007). Provenance semirings. *PODS 2007*. **verify before citing**
20. Croushore, D., & Stark, T. (2001). A real-time data set for macroeconomists. *Journal of Econometrics*, 105(1).
    It is the reference point for analysing data by vintage. **verify before citing**

## Financial data errors, revisions and survivorship

21. Rosenberg, B., & Houglet, M. (1974). Error rates in CRSP and Compustat data bases and their implications. *Journal
    of Finance*, 29(4), 1303–1310. **checked** ([RePEc](https://ideas.repec.org/a/bla/jfinan/v29y1974i4p1303-10.html))
22. Brown, S. J., Goetzmann, W., Ibbotson, R. G., & Ross, S. A. (1992). Survivorship bias in performance studies.
    *Review of Financial Studies*, 5(4). **verify before citing**
23. Shumway, T. (1997). The delisting bias in CRSP data. *Journal of Finance*, 52(1). **verify before citing**
24. Canina, L., Michaely, R., Thaler, R., & Womack, K. (1998). Caveat compounder: A warning about using the daily CRSP
    equal-weighted index to compute long-run excess returns. *Journal of Finance*, 53(1). **verify before citing**
25. Ince, O. S., & Porter, R. B. (2006). Individual equity return data from Thomson Datastream: Handle with care!
    *Journal of Financial Research*, 29(4). **verify before citing**
26. Ljungqvist, A., Malloy, C., & Marston, F. (2009). Rewriting history. *Journal of Finance*, 64(4). It covers
    retroactive changes to I/B/E/S data. **verify before citing**

## Backtest overfitting and inference on performance

27. Lo, A. W., & MacKinlay, A. C. (1990). Data-snooping biases in tests of financial asset pricing models. *Review of
    Financial Studies*, 3(3). **verify before citing**
28. White, H. (2000). A reality check for data snooping. *Econometrica*, 68(5). **verify before citing**
29. Lo, A. W. (2002). The statistics of Sharpe ratios. *Financial Analysts Journal*, 58(4). **verify before citing**
30. Hansen, P. R. (2005). A test for superior predictive ability. *Journal of Business & Economic Statistics*, 23(4).
    **verify before citing**
31. Ledoit, O., & Wolf, M. (2008). Robust performance hypothesis testing with the Sharpe ratio. *Journal of Empirical
    Finance*, 15(5). **verify before citing**
32. Bailey, D. H., & López de Prado, M. (2012). The Sharpe ratio efficient frontier. *Journal of Risk*, 15(2), 3–44.
    doi:10.21314/jor.2012.255. It is the source of the probabilistic Sharpe ratio used in `deflated_sharpe()`.
    **checked** ([Risk.net](https://www.risk.net/journal-risk/2223785/sharpe-ratio-efficient-frontier))
33. Bailey, D. H., & López de Prado, M. (2014). The deflated Sharpe ratio: Correcting for selection bias, backtest
    overfitting and non-normality. *Journal of Portfolio Management*, 40(5). It is the formula used in
    `src/backtest_rigour.py`. **verify before citing**
34. Bailey, D. H., Borwein, J. M., López de Prado, M., & Zhu, Q. J. (2014). Pseudo-mathematics and financial
    charlatanism: The effects of backtest overfitting on out-of-sample performance. *Notices of the AMS*, 61(5).
    **verify before citing**
35. Bailey, D. H., Borwein, J. M., López de Prado, M., & Zhu, Q. J. (2017). The probability of backtest overfitting.
    *Journal of Computational Finance*, 20(4). **verify before citing**
36. Harvey, C. R., Liu, Y., & Zhu, H. (2016). … and the cross-section of expected returns. *Review of Financial
    Studies*, 29(1). **verify before citing**
37. McLean, R. D., & Pontiff, J. (2016). Does academic research destroy stock return predictability? *Journal of
    Finance*, 71(1). **verify before citing**
38. Hou, K., Xue, C., & Zhang, L. (2020). Replicating anomalies. *Review of Financial Studies*, 33(5).
    **verify before citing**
39. López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. **verify before citing**

## Bootstrap for dependent data

40. Künsch, H. R. (1989). The jackknife and the bootstrap for general stationary observations. *Annals of Statistics*,
    17(3). It introduces the moving-block bootstrap used here. **verify before citing**
41. Politis, D. N., & Romano, J. P. (1994). The stationary bootstrap. *Journal of the American Statistical
    Association*, 89(428). **verify before citing**
42. Efron, B., & Tibshirani, R. J. (1993). *An Introduction to the Bootstrap*. Chapman & Hall. **verify before citing**

## Agreement between labellers and intervals for proportions

43. Cohen, J. (1960). A coefficient of agreement for nominal scales. *Educational and Psychological Measurement*,
    20(1). **verify before citing**
44. Landis, J. R., & Koch, G. G. (1977). The measurement of observer agreement for categorical data. *Biometrics*,
    33(1). **verify before citing**
45. Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *Journal of the
    American Statistical Association*, 22(158). **verify before citing**

## Strategy background (only if the paper discusses the signals)

46. Jegadeesh, N., & Titman, S. (1993). Returns to buying winners and selling losers: Implications for stock market
    efficiency. *Journal of Finance*, 48(1). **verify before citing**
47. Moskowitz, T. J., & Grinblatt, M. (1999). Do industries explain momentum? *Journal of Finance*, 54(4).
    **verify before citing**

## Gaps: search before writing, do not cite from memory

- Studies that measure the accuracy, revisions or corporate-action handling of **Yahoo Finance** or other free
  end-of-day data. I know of no specific paper well enough to list one.
- Studies of data quality or survivorship in **Indian equity** databases (CMIE Prowess, NSE, BSE). The same applies.
- Work on **point-in-time vendor archives**, such as commercial "as-reported" or point-in-time datasets, as described
  in peer-reviewed sources rather than vendor marketing.
