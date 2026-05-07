
public class QuickSort {
    
    /**
     * 快速排序主方法
     * @param arr 要排序的数组
     */
    public static void quickSort(int[] arr) {
        if (arr == null || arr.length <= 1) {
            return;
        }
        quickSort(arr, 0, arr.length - 1);
    }
    
    /**
     * 快速排序递归方法
     * @param arr   数组
     * @param left  左边索引
     * @param right 右边索引
     */
    private static void quickSort(int[] arr, int left, int right) {
        if (left < right) {
            // 获取分区后的基准位置
            int pivotIndex = partition(arr, left, right);
            // 递归排序左半部分
            quickSort(arr, left, pivotIndex - 1);
            // 递归排序右半部分
            quickSort(arr, pivotIndex + 1, right);
        }
    }
    
    /**
     * 分区操作（使用 Lomuto 划分方案）
     * @param arr   数组
     * @param left  左边索引
     * @param right 右边索引
     * @return 基准元素的最终位置
     */
    private static int partition(int[] arr, int left, int right) {
        // 选择最右侧元素作为基准
        int pivot = arr[right];
        // i 用于划分小于基准和大于基准的区域
        int i = left - 1;
        
        for (int j = left; j < right; j++) {
            if (arr[j] <= pivot) {
                i++;
                // 交换 arr[i] 和 arr[j]
                swap(arr, i, j);
            }
        }
        
        // 将基准放到正确位置
        swap(arr, i + 1, right);
        return i + 1;
    }
    
    /**
     * 交换数组中两个元素的位置
     */
    private static void swap(int[] arr, int i, int j) {
        int temp = arr[i];
        arr[i] = arr[j];
        arr[j] = temp;
    }
    
    // ==================== 测试代码 ====================
    public static void main(String[] args) {
        // 测试用例1：普通数组
        int[] arr1 = {64, 34, 25, 12, 22, 11, 90};
        System.out.println("原始数组: ");
        printArray(arr1);
        
        quickSort(arr1);
        
        System.out.println("排序后: ");
        printArray(arr1);
        
        // 测试用例2：包含重复元素的数组
        int[] arr2 = {5, 2, 9, 2, 6, 2, 7};
        System.out.println("\n原始数组（含重复）: ");
        printArray(arr2);
        
        quickSort(arr2);
        
        System.out.println("排序后: ");
        printArray(arr2);
        
        // 测试用例3：已排序的数组
        int[] arr3 = {1, 2, 3, 4, 5};
        System.out.println("\n已排序数组: ");
        printArray(arr3);
        
        quickSort(arr3);
        
        System.out.println("排序后: ");
        printArray(arr3);
        
        // 测试用例4：逆序数组
        int[] arr4 = {5, 4, 3, 2, 1};
        System.out.println("\n逆序数组: ");
        printArray(arr4);
        
        quickSort(arr4);
        
        System.out.println("排序后: ");
        printArray(arr4);
    }
    
    /**
     * 打印数组
     */
    private static void printArray(int[] arr) {
        for (int num : arr) {
            System.out.print(num + " ");
        }
        System.out.println();
    }
}
